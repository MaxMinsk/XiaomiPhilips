"""Local Home Assistant integration for Xiaomi Philips EyeCare."""

import logging

import probatio as vol
from homeassistant.const import CONF_HOST, CONF_TOKEN, Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError

from .api import LampApi
from .const import DOMAIN
from .coordinator import LampCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT]
SERVICE_PROBE = "probe"
PROBE_SCHEMA = vol.Schema({vol.Optional("entry_id"): str})


async def async_setup(hass, config) -> bool:
    async def async_probe(call):
        """Report what the lamp answers right now, even if setup failed."""
        entries = hass.config_entries.async_entries(DOMAIN)
        entry_id = call.data.get("entry_id")
        if entry_id:
            entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError("No Philips EyeCare configuration to probe")

        report = {}
        for entry in entries:
            # Build a fresh client: a failed entry has no coordinator to borrow.
            api = LampApi(entry.data[CONF_HOST], entry.data[CONF_TOKEN])
            result = await hass.async_add_executor_job(api.probe)
            report[entry.title] = result
            _LOGGER.warning("Probe of %s: %s", entry.title, result)
        return report

    hass.services.async_register(
        DOMAIN,
        SERVICE_PROBE,
        async_probe,
        schema=PROBE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass, entry) -> bool:
    api = LampApi(entry.data[CONF_HOST], entry.data[CONF_TOKEN])
    coordinator = LampCoordinator(hass, entry, api)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # HA retries a failed entry on a backoff, and each probe costs a timeout per
        # property, so explain the failure only while someone is watching debug logs.
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "First poll of %s failed; probe result: %s",
                entry.title,
                await hass.async_add_executor_job(api.probe),
            )
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
