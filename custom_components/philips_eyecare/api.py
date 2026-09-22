"""Synchronous local miIO access; called only in executor threads."""

import logging
import time
from dataclasses import dataclass, field
from threading import RLock

from miio import DeviceException, PhilipsEyecare
from miio.exceptions import DeviceError, PayloadDecodeException

from .const import SUPPORTED_MODELS

_LOGGER = logging.getLogger(__name__)

# Mirrors the property list python-miio sends for this model, so a probe can
# ask for the same names one by one and see which ones the firmware answers.
STATUS_PROPERTIES = (
    "power",
    "bright",
    "notifystatus",
    "ambstatus",
    "ambvalue",
    "eyecare",
    "scene_num",
    "bls",
    "dvalue",
)

NO_HANDSHAKE = "no_handshake"
TOKEN_REJECTED = "token_rejected"
DEVICE_ERROR = "device_error"
NETWORK_ERROR = "network_error"
BAD_PAYLOAD = "bad_payload"
UNKNOWN_ERROR = "unknown_error"

ERROR_HINTS = {
    NO_HANDSHAKE: (
        "No answer to the miIO handshake on UDP 54321. The lamp is either unreachable "
        "from the Home Assistant host (VLAN, guest network, client isolation, Docker "
        "bridge networking) or busy talking to another client."
    ),
    TOKEN_REJECTED: (
        "The lamp answered but the reply could not be decrypted. The token is wrong or "
        "stale; re-read it after the last reset or re-pairing."
    ),
    DEVICE_ERROR: "The lamp answered with an error to a command it did not accept.",
    NETWORK_ERROR: "The socket to the lamp failed before a reply arrived.",
    BAD_PAYLOAD: "The lamp answered with a payload python-miio could not parse.",
    UNKNOWN_ERROR: "Unclassified failure; see the debug log for the original exception.",
}


def describe_error(err: BaseException) -> str:
    """Classify a miIO failure into a cause that is worth showing to a user."""
    text = str(err).lower()
    if isinstance(err, PayloadDecodeException):
        return BAD_PAYLOAD
    if "checksum" in text or "decrypt" in text or "token" in text:
        return TOKEN_REJECTED
    if isinstance(err, TimeoutError) or "timeout" in text or "unable to discover" in text:
        return NO_HANDSHAKE
    if isinstance(err, DeviceError):
        return DEVICE_ERROR
    if isinstance(err, OSError):
        return NETWORK_ERROR
    return UNKNOWN_ERROR


class UnsupportedModel(Exception):
    """The address belongs to another model."""

    def __init__(self, model: str | None = None) -> None:
        super().__init__(f"Unsupported model {model!r}")
        self.model = model


class InvalidResponse(Exception):
    """The lamp did not return a usable response."""

    def __init__(self, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload


@dataclass(frozen=True)
class LampInfo:
    model: str
    mac: str
    firmware: str | None


@dataclass(frozen=True)
class LampState:
    main_on: bool
    main_brightness: int
    ambient_on: bool
    ambient_brightness: int


@dataclass
class LastExchange:
    """What the most recent miIO exchange did, for diagnostics and logs."""

    call: str = ""
    ok: bool = False
    duration_ms: int = 0
    cause: str | None = None
    detail: str | None = None
    payload: dict | None = field(default=None)


def brightness_to_percent(value: int) -> int:
    """Map a nonzero HA brightness to the lamp's 1..100 range."""
    return max(1, min(100, round(value * 100 / 255)))


class LampApi:
    """Serialize polls and complete command sequences on one device."""

    def __init__(self, host: str, token: str) -> None:
        self._device = PhilipsEyecare(host, token, timeout=3, model="philips.light.sread2")
        self._lock = RLock()
        self._host = host
        self.last_exchange = LastExchange()

    def _record(self, call, started, ok, err=None, payload=None) -> None:
        duration_ms = round((time.monotonic() - started) * 1000)
        cause = describe_error(err) if err is not None else None
        self.last_exchange = LastExchange(
            call=call,
            ok=ok,
            duration_ms=duration_ms,
            cause=cause,
            detail=f"{type(err).__name__}: {err}" if err is not None else None,
            payload=payload,
        )
        if ok:
            _LOGGER.debug("%s on %s took %d ms, payload %s", call, self._host, duration_ms, payload)
        else:
            # Payload and exception text can carry protocol data, so keep them at debug.
            _LOGGER.debug(
                "%s on %s failed after %d ms: %s (%s); payload %s",
                call,
                self._host,
                duration_ms,
                cause,
                self.last_exchange.detail,
                payload,
                exc_info=err,
            )

    def info(self) -> LampInfo:
        with self._lock:
            started = time.monotonic()
            try:
                info = self._device.info()
            except Exception as err:
                self._record("info", started, False, err)
                raise
            raw = dict(getattr(info, "raw", None) or {})
            raw.pop("token", None)
            self._record("info", started, True, payload=raw)
            if info.model not in SUPPORTED_MODELS:
                _LOGGER.warning(
                    "Lamp at %s reports model %s; this integration supports %s",
                    self._host,
                    info.model,
                    ", ".join(sorted(SUPPORTED_MODELS)),
                )
                raise UnsupportedModel(info.model)
            mac = (info.mac_address or "").replace(":", "").replace("-", "").lower()
            if len(mac) != 12 or any(c not in "0123456789abcdef" for c in mac):
                _LOGGER.warning(
                    "Lamp at %s did not report a usable MAC address; "
                    "enable debug logging for philips_eyecare to see the reply",
                    self._host,
                )
                raise InvalidResponse("No valid device identity", raw)
            return LampInfo(info.model, mac, info.firmware_version)

    def status(self) -> LampState:
        with self._lock:
            started = time.monotonic()
            try:
                status = self._device.status()
            except Exception as err:
                self._record("status", started, False, err)
                raise
            data = dict(status.data)
            self._record("status", started, True, payload=data)
            missing = [name for name in STATUS_PROPERTIES if data.get(name) is None]
            if missing:
                _LOGGER.debug(
                    "Lamp at %s did not answer properties %s", self._host, ", ".join(missing)
                )
            # Do not display a disconnected or incomplete light as switched off.
            for key in ("power", "ambstatus"):
                if data.get(key) not in ("on", "off"):
                    raise InvalidResponse(f"Property {key} was {data.get(key)!r}", data)
            for key in ("bright", "ambvalue"):
                value = data.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise InvalidResponse(f"Property {key} was {value!r}", data)
                if not 0 <= value <= 100:
                    raise InvalidResponse(f"Property {key} was out of range: {value!r}", data)
            return LampState(
                status.is_on,
                round(status.brightness),
                status.ambient,
                round(status.ambient_brightness),
            )

    def validate(self) -> LampInfo:
        """Check identity and light properties before saving a configuration."""
        with self._lock:
            info = self.info()
            self.status()
            return info

    def probe(self) -> dict:
        """Collect a full picture of one lamp without raising; used by diagnostics."""
        report: dict = {"host_configured": bool(self._host), "steps": {}}
        with self._lock:
            started = time.monotonic()
            try:
                self._device.send_handshake()
                report["steps"]["handshake"] = {
                    "ok": True,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "device_id": getattr(self._device, "device_id", None),
                }
            except Exception as err:
                report["steps"]["handshake"] = {
                    "ok": False,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "cause": describe_error(err),
                    "detail": f"{type(err).__name__}: {err}",
                }
                return report

            try:
                info = self._device.info()
                report["steps"]["info"] = {
                    "ok": True,
                    "model": info.model,
                    "firmware": info.firmware_version,
                    "hardware": getattr(info, "hardware_version", None),
                    "supported": info.model in SUPPORTED_MODELS,
                }
            except Exception as err:
                report["steps"]["info"] = {
                    "ok": False,
                    "cause": describe_error(err),
                    "detail": f"{type(err).__name__}: {err}",
                }

            try:
                values = self._device.get_properties(list(STATUS_PROPERTIES))
                report["steps"]["get_prop_batch"] = {
                    "ok": True,
                    "requested": len(STATUS_PROPERTIES),
                    "received": len(values),
                    "values": dict(zip(STATUS_PROPERTIES, values)),
                }
            except Exception as err:
                report["steps"]["get_prop_batch"] = {
                    "ok": False,
                    "cause": describe_error(err),
                    "detail": f"{type(err).__name__}: {err}",
                }

            # A firmware that drops the batch may still answer properties one by one.
            per_property: dict[str, object] = {}
            for name in STATUS_PROPERTIES:
                try:
                    per_property[name] = self._device.send("get_prop", [name])
                except Exception as err:
                    per_property[name] = f"{describe_error(err)}: {type(err).__name__}"
            report["steps"]["get_prop_single"] = per_property
        return report

    @staticmethod
    def _check(result) -> None:
        if result != ["ok"]:
            raise DeviceException(f"Lamp rejected command, replied {result!r}")

    def set_light(self, ambient: bool, on: bool, brightness: int | None = None) -> None:
        with self._lock:
            level = None if brightness is None else brightness_to_percent(brightness)
            _LOGGER.debug(
                "Sending to %s: ambient=%s on=%s brightness=%s (%s%%)",
                self._host,
                ambient,
                on,
                brightness,
                level,
            )
            started = time.monotonic()
            try:
                if not on or brightness == 0:
                    self._check(self._device.ambient_off() if ambient else self._device.off())
                    self._record("set_light", started, True)
                    return
                # Explicitly power on: changing brightness alone is not assumed to do so.
                self._check(self._device.ambient_on() if ambient else self._device.on())
                if brightness is not None:
                    setter = (
                        self._device.set_ambient_brightness
                        if ambient
                        else self._device.set_brightness
                    )
                    self._check(setter(level))
            except Exception as err:
                self._record("set_light", started, False, err)
                raise
            self._record("set_light", started, True)
