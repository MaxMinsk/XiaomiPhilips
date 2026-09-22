"""Exercise real python-miio parsing and commands with a fake lamp transport."""

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from miio import DeviceException

from custom_components.philips_eyecare.api import (
    InvalidResponse,
    LampApi,
    UnsupportedModel,
    describe_error,
)


def raised_by_miio(message, cause=None):
    """Rebuild what python-miio raises, including the exception it chains from."""
    try:
        if cause is None:
            raise DeviceException(message)
        try:
            raise cause
        except type(cause) as inner:
            raise DeviceException(message) from inner
    except DeviceException as err:
        return err


@pytest.mark.parametrize(
    "error,expected",
    [
        (raised_by_miio("Unable to discover the device 192.0.2.10"), "no_handshake"),
        # A silent lamp reports "timed out", which never spells the word timeout.
        (raised_by_miio("No response from the device", TimeoutError("timed out")), "no_reply"),
        (raised_by_miio("Unable to recover failed command"), "no_reply"),
        (
            raised_by_miio("Got checksum error which indicates use of an invalid token."),
            "token_rejected",
        ),
        (TimeoutError("timed out"), "no_handshake"),
        (OSError("network is unreachable"), "network_error"),
    ],
)
def test_every_documented_failure_is_classified(error, expected):
    assert describe_error(error) == expected


@pytest.fixture
def api():
    return LampApi("192.0.2.10", "0" * 32)


def test_status_uses_actual_miio_parser(api):
    api._device.get_properties = Mock(return_value=["on", 50, "off", "off", 25, "on", 1, "off", 0])
    state = api.status()
    assert (state.main_on, state.main_brightness) == (True, 50)
    assert (state.ambient_on, state.ambient_brightness) == (False, 25)


@pytest.mark.parametrize(
    "values",
    [
        [],
        ["on"],
        ["bad", 50, "off", "on", 20],
        ["on", None, "off", "on", 20],
        ["on", 101, "off", "on", 20],
    ],
)
def test_incomplete_response_is_not_reported_as_off(api, values):
    api._device.get_properties = Mock(return_value=values)
    with pytest.raises(InvalidResponse):
        api.status()


@pytest.mark.parametrize(
    "ambient,on,brightness,expected",
    [
        (False, True, 128, [("set_power", ["on"]), ("set_bright", [50])]),
        (True, True, 255, [("enable_amb", ["on"]), ("set_amb_bright", [100])]),
        (False, True, 1, [("set_power", ["on"]), ("set_bright", [1])]),
        (False, True, None, [("set_power", ["on"])]),
        (True, False, None, [("enable_amb", ["off"])]),
        (False, True, 0, [("set_power", ["off"])]),
    ],
)
def test_wire_commands(api, ambient, on, brightness, expected):
    api._device.send = Mock(return_value=["ok"])
    api.set_light(ambient, on, brightness)
    assert [c.args for c in api._device.send.call_args_list] == expected


def test_rejected_command_is_not_silently_accepted(api):
    api._device.send = Mock(return_value=["error"])
    with pytest.raises(DeviceException):
        api.set_light(False, True, 100)
    assert api._device.send.call_count == 1


def test_identity_and_wrong_model(api):
    api._device.info = Mock(
        return_value=SimpleNamespace(
            model="philips.light.sread2", mac_address="AA:BB:CC:DD:EE:FF", firmware_version="2.1.3"
        )
    )
    assert api.info().mac == "aabbccddeeff"
    api._device.info.return_value.model = "yeelink.light.color1"
    with pytest.raises(UnsupportedModel):
        api.info()


def test_lamp_that_ignores_info_is_still_usable(api):
    """A firmware may answer get_prop and never answer miIO.info."""
    api._device.info = Mock(side_effect=raised_by_miio("No response from the device"))
    api._device.get_properties = Mock(return_value=["on", 50, "off", "off", 25, "on", 1, "off", 0])
    with patch.object(type(api._device), "device_id", property(lambda self: 0x1A2B3C4D)):
        info = api.validate()
    assert info.identified_by == "handshake"
    assert info.mac == "did00001a2b3c4d"


def test_a_lamp_that_answers_nothing_still_fails(api):
    api._device.info = Mock(side_effect=raised_by_miio("No response from the device"))
    api._device.get_properties = Mock(side_effect=raised_by_miio("No response from the device"))
    with pytest.raises(DeviceException):
        api.validate()


def test_command_sequences_do_not_interleave(api):
    calls = []

    def send(method, params):
        calls.append(method)
        time.sleep(0.005)
        return ["ok"]

    api._device.send = send
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(api.set_light, ambient, True, 128) for ambient in (False, True)]
        for future in futures:
            future.result()
    assert calls in (
        ["set_power", "set_bright", "enable_amb", "set_amb_bright"],
        ["enable_amb", "set_amb_bright", "set_power", "set_bright"],
    )
