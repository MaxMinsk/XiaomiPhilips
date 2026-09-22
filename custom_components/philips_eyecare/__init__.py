"""Local Home Assistant integration for Xiaomi Philips EyeCare."""

from homeassistant.const import CONF_HOST, CONF_TOKEN, Platform

from .api import LampApi
from .coordinator import LampCoordinator

PLATFORMS = [Platform.LIGHT]


async def async_setup_entry(hass, entry) -> bool:
    api = LampApi(entry.data[CONF_HOST], entry.data[CONF_TOKEN])
    coordinator = LampCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
