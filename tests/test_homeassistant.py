"""Test against real HA classes while mocking only network/executor boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from miio import DeviceException

from custom_components.philips_eyecare.api import LampInfo, LampState
from custom_components.philips_eyecare.config_flow import EyeCareConfigFlow
from custom_components.philips_eyecare.coordinator import LampCoordinator
from custom_components.philips_eyecare.light import EyeCareLight

INPUT = {"host": "192.0.2.10", "token": "a" * 32, "name": "Desk lamp"}
INFO = LampInfo("philips.light.sread2", "aabbccddeeff", "2.1.3")


def flow_with_result(result=INFO):
    flow = EyeCareConfigFlow()
    flow.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=result))
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow.async_create_entry = Mock(side_effect=lambda **kw: kw)
    flow.async_show_form = Mock(side_effect=lambda **kw: kw)
    return flow


async def test_setup_saves_identity_and_metadata():
    flow = flow_with_result()
    result = await flow.async_step_user(INPUT)
    assert result["data"]["model"] == "philips.light.sread2"
    assert result["data"]["firmware"] == "2.1.3"
    flow.async_set_unique_id.assert_awaited_once_with("aabbccddeeff")


async def test_invalid_token_does_not_touch_network():
    flow = flow_with_result()
    result = await flow.async_step_user({**INPUT, "token": "b" + "a" * 32})
    assert result["errors"] == {"token": "invalid_token"}
    flow.hass.async_add_executor_job.assert_not_called()


@pytest.mark.parametrize("token", ["f" * 32, "F" * 32, "0" * 32])
async def test_handshake_marker_is_not_accepted_as_a_token(token):
    """A provisioned lamp answers ff..ff, and that is a refusal, not a token."""
    flow = flow_with_result()
    result = await flow.async_step_user({**INPUT, "token": token})
    assert result["errors"] == {"token": "placeholder_token"}
    flow.hass.async_add_executor_job.assert_not_called()


async def test_unreachable_device_keeps_form_open_without_leaking_secret():
    flow = flow_with_result()
    flow.hass.async_add_executor_job.side_effect = DeviceException(INPUT["token"])
    result = await flow.async_step_user(INPUT)
    assert result["errors"] == {"base": "cannot_connect"}
    assert INPUT["token"] not in repr(result)


async def test_duplicate_lamp_is_not_created_twice():
    flow = flow_with_result()
    flow._abort_if_unique_id_configured.side_effect = AbortFlow("already_configured")
    with pytest.raises(AbortFlow):
        await flow.async_step_user(INPUT)
    flow.async_create_entry.assert_not_called()


async def test_reconfigure_preserves_token_and_device_identity():
    flow = flow_with_result()
    entry = SimpleNamespace(data=INPUT, unique_id=INFO.mac)
    flow._get_reconfigure_entry = Mock(return_value=entry)
    flow.async_update_reload_and_abort = Mock(return_value={"type": "abort"})
    await flow.async_step_reconfigure({"host": "192.0.2.11", "name": "Desk"})
    updates = flow.async_update_reload_and_abort.call_args.kwargs["data_updates"]
    assert updates["host"] == "192.0.2.11"
    assert updates["token"] == INPUT["token"]


async def test_reconfigure_rejects_a_different_lamp():
    flow = flow_with_result()
    flow._get_reconfigure_entry = Mock(
        return_value=SimpleNamespace(data=INPUT, unique_id="112233445566")
    )
    result = await flow.async_step_reconfigure(INPUT)
    assert result["errors"]["base"] == "wrong_device"


def fake_entry():
    coordinator = SimpleNamespace(
        data=LampState(True, 50, False, 25), last_update_success=True, async_set_light=AsyncMock()
    )
    return SimpleNamespace(
        runtime_data=coordinator,
        unique_id=INFO.mac,
        data={**INPUT, "model": INFO.model, "firmware": INFO.firmware},
    )


async def test_two_entities_share_one_device_and_route_commands():
    entry = fake_entry()
    main, ambient = EyeCareLight(entry, False), EyeCareLight(entry, True)
    assert main.device_info == ambient.device_info
    assert main.unique_id != ambient.unique_id
    assert main.is_on and not ambient.is_on
    assert main.brightness == 128
    await ambient.async_turn_on(brightness=200)
    entry.runtime_data.async_set_light.assert_awaited_once_with(True, True, 200)
    entry.runtime_data.last_update_success = False
    assert not main.available and not ambient.available


async def test_coordinator_recovers_after_network_failure():
    with patch(
        "custom_components.philips_eyecare.coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coordinator = LampCoordinator(None, None, Mock())
    coordinator.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(
            side_effect=[DeviceException("timeout"), LampState(True, 50, False, 25)]
        )
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert (await coordinator._async_update_data()).main_on


async def test_command_failure_is_visible_to_user():
    with patch(
        "custom_components.philips_eyecare.coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coordinator = LampCoordinator(None, None, Mock())
    coordinator.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=DeviceException("timeout"))
    )
    coordinator.async_set_update_error = Mock()
    coordinator.async_request_refresh = AsyncMock()
    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_light(False, True)
    coordinator.async_set_update_error.assert_called_once()
    coordinator.async_request_refresh.assert_not_called()


def test_flow_is_registered():
    assert config_entries.HANDLERS.get("philips_eyecare") is EyeCareConfigFlow
