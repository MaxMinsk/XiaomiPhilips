"""Main and ambient lights belonging to one registered lamp."""

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import CONF_MODEL, CONF_NAME
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME

PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([EyeCareLight(entry, False), EyeCareLight(entry, True)])


class EyeCareLight(CoordinatorEntity, LightEntity):
    _attr_has_entity_name = True
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, entry, ambient: bool) -> None:
        super().__init__(entry.runtime_data)
        self._ambient = ambient
        key = "ambient" if ambient else "main"
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.data.get(CONF_NAME, NAME),
            manufacturer="Xiaomi / Philips",
            model=entry.data[CONF_MODEL],
            sw_version=entry.data.get("firmware"),
        )

    @property
    def is_on(self) -> bool:
        state = self.coordinator.data
        return state.ambient_on if self._ambient else state.main_on

    @property
    def brightness(self) -> int:
        state = self.coordinator.data
        value = state.ambient_brightness if self._ambient else state.main_brightness
        return max(0, min(255, round(value * 255 / 100)))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_light(self._ambient, True, kwargs.get(ATTR_BRIGHTNESS))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_light(self._ambient, False)
