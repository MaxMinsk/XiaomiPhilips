"""Shareable diagnostics with no token, address, or device identifier."""

from dataclasses import asdict


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    return {
        "integration_version": "0.1.0",
        "model": entry.data.get("model"),
        "firmware": entry.data.get("firmware"),
        "last_update_success": coordinator.last_update_success if coordinator else None,
        "state": asdict(coordinator.data) if coordinator and coordinator.data else None,
    }
