"""Synchronous local miIO access; called only in executor threads."""

from dataclasses import dataclass
from threading import RLock

from miio import DeviceException, PhilipsEyecare

from .const import SUPPORTED_MODELS


class UnsupportedModel(Exception):
    """The address belongs to another model."""


class InvalidResponse(Exception):
    """The lamp did not return a usable response."""


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


def brightness_to_percent(value: int) -> int:
    """Map a nonzero HA brightness to the lamp's 1..100 range."""
    return max(1, min(100, round(value * 100 / 255)))


class LampApi:
    """Serialize polls and complete command sequences on one device."""

    def __init__(self, host: str, token: str) -> None:
        self._device = PhilipsEyecare(host, token, timeout=3, model="philips.light.sread2")
        self._lock = RLock()

    def info(self) -> LampInfo:
        with self._lock:
            info = self._device.info()
            if info.model not in SUPPORTED_MODELS:
                raise UnsupportedModel
            mac = (info.mac_address or "").replace(":", "").replace("-", "").lower()
            if len(mac) != 12 or any(c not in "0123456789abcdef" for c in mac):
                raise InvalidResponse("No valid device identity")
            return LampInfo(info.model, mac, info.firmware_version)

    def status(self) -> LampState:
        with self._lock:
            status = self._device.status()
            data = status.data
            # Do not display a disconnected or incomplete light as switched off.
            for key in ("power", "ambstatus"):
                if data.get(key) not in ("on", "off"):
                    raise InvalidResponse("Incomplete power state")
            for key in ("bright", "ambvalue"):
                value = data.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise InvalidResponse("Incomplete brightness state")
                if not 0 <= value <= 100:
                    raise InvalidResponse("Brightness outside expected range")
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

    @staticmethod
    def _check(result) -> None:
        if result != ["ok"]:
            raise DeviceException("Lamp rejected command")

    def set_light(self, ambient: bool, on: bool, brightness: int | None = None) -> None:
        with self._lock:
            if not on or brightness == 0:
                self._check(self._device.ambient_off() if ambient else self._device.off())
                return
            # Explicitly power on: changing brightness alone is not assumed to do so.
            self._check(self._device.ambient_on() if ambient else self._device.on())
            if brightness is not None:
                level = brightness_to_percent(brightness)
                setter = (
                    self._device.set_ambient_brightness if ambient else self._device.set_brightness
                )
                self._check(setter(level))
