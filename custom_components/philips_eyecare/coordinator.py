"""One local poll for both light entities."""

import logging
from datetime import timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from miio import DeviceException

from .api import ERROR_HINTS, InvalidResponse, LampApi, LampState, describe_error
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
        self.consecutive_failures = 0

    def _explain(self, err: Exception) -> str:
        if isinstance(err, InvalidResponse):
            return f"the lamp answered but {err}"
        cause = describe_error(err)
        return f"{cause} - {ERROR_HINTS[cause]}"

    async def _async_update_data(self) -> LampState:
        try:
            state = await self.hass.async_add_executor_job(self.api.status)
        except (DeviceException, OSError, InvalidResponse) as err:
            self.consecutive_failures += 1
            reason = self._explain(err)
            # Library errors may contain protocol data; keep the raw text at debug.
            _LOGGER.debug(
                "Poll %d in a row failed: %s", self.consecutive_failures, err, exc_info=err
            )
            raise UpdateFailed(f"Cannot read lamp state: {reason}") from err
        if self.consecutive_failures:
            _LOGGER.info("Lamp answered again after %d failed polls", self.consecutive_failures)
            self.consecutive_failures = 0
        return state

    async def async_set_light(self, ambient: bool, on: bool, brightness=None) -> None:
        try:
            await self.hass.async_add_executor_job(self.api.set_light, ambient, on, brightness)
        except (DeviceException, OSError, InvalidResponse) as err:
            reason = self._explain(err)
            _LOGGER.debug(
                "Command ambient=%s on=%s brightness=%s failed: %s",
                ambient,
                on,
                brightness,
                err,
                exc_info=err,
            )
            self.async_set_update_error(UpdateFailed(f"Lamp command failed: {reason}"))
            raise HomeAssistantError(f"Lamp did not accept the command: {reason}") from err
        await self.async_request_refresh()
