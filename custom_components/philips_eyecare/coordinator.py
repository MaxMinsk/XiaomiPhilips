"""One local poll for both light entities."""

import logging
from datetime import timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from miio import DeviceException

from .api import InvalidResponse, LampApi, LampState
from .const import NAME, UPDATE_SECONDS

_LOGGER = logging.getLogger(__name__)


class LampCoordinator(DataUpdateCoordinator[LampState]):
    def __init__(self, hass, entry, api: LampApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=NAME,
            update_interval=timedelta(seconds=UPDATE_SECONDS),
            always_update=False,
        )
        self.api = api

    async def _async_update_data(self) -> LampState:
        try:
            return await self.hass.async_add_executor_job(self.api.status)
        except (DeviceException, OSError, InvalidResponse) as err:
            # Library errors may contain protocol data; don't include them in logs.
            raise UpdateFailed("Cannot read lamp state; check connection and token") from err

    async def async_set_light(self, ambient: bool, on: bool, brightness=None) -> None:
        try:
            await self.hass.async_add_executor_job(self.api.set_light, ambient, on, brightness)
        except (DeviceException, OSError, InvalidResponse) as err:
            self.async_set_update_error(UpdateFailed("Lamp command failed"))
            raise HomeAssistantError(
                "Lamp did not accept the command; check connection and token"
            ) from err
        await self.async_request_refresh()
