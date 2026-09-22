"""Shareable diagnostics with no token, address, or device identifier."""

from dataclasses import asdict

from homeassistant.const import CONF_HOST, CONF_TOKEN

from .api import LampApi


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    # A lamp that never set up has no coordinator, and that is the case worth probing.
    api = coordinator.api if coordinator else LampApi(entry.data[CONF_HOST], entry.data[CONF_TOKEN])
    probe = await hass.async_add_executor_job(api.probe)
    # device_id identifies the lamp; drop it before the report leaves the house.
    probe.get("steps", {}).get("handshake", {}).pop("device_id", None)
    return {
        "integration_version": "0.1.0",
        "model": entry.data.get("model"),
        "firmware": entry.data.get("firmware"),
        "entry_state": str(entry.state),
        "last_update_success": coordinator.last_update_success if coordinator else None,
        "consecutive_failures": coordinator.consecutive_failures if coordinator else None,
        "last_error": str(coordinator.last_exception) if coordinator else None,
        "last_exchange": asdict(api.last_exchange),
        "state": asdict(coordinator.data) if coordinator and coordinator.data else None,
        "probe": probe,
    }
