"""Load the custom integration through a real Home Assistant config flow."""

import shutil
from pathlib import Path
from unittest.mock import patch

from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry, frame

from custom_components.philips_eyecare.api import LampInfo, LampState


async def test_real_ha_setup_creates_device_and_two_lights(tmp_path):
    shutil.copytree(
        Path(__file__).parents[1] / "custom_components",
        tmp_path / "custom_components",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    frame.async_setup(hass)
    device_registry.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    await area_registry.async_load(hass)
    await device_registry.async_load(hass)
    await entity_registry.async_load(hass)
    try:
        with (
            patch(
                "custom_components.philips_eyecare.api.LampApi.validate",
                return_value=LampInfo("philips.light.sread2", "aabbccddeeff", "2.1.3"),
            ),
            patch(
                "custom_components.philips_eyecare.api.LampApi.status",
                return_value=LampState(True, 50, False, 25),
            ),
            patch("custom_components.philips_eyecare.api.LampApi.set_light") as command,
        ):
            result = await hass.config_entries.flow.async_init(
                "philips_eyecare",
                context={"source": config_entries.SOURCE_USER},
                data={"host": "192.0.2.10", "token": "0123456789abcdef" * 2, "name": "Desk lamp"},
            )
            await hass.async_block_till_done()
            assert result["type"] == "create_entry"
            entry = result["result"]
            assert entry.state == config_entries.ConfigEntryState.LOADED
            entities = entity_registry.async_entries_for_config_entry(
                entity_registry.async_get(hass), entry.entry_id
            )
            assert len(entities) == 2
            assert len({entity.device_id for entity in entities}) == 1
            assert all(entity.device_id for entity in entities)
            main = next(e for e in entities if e.unique_id.endswith("_main"))
            assert hass.states.get(main.entity_id).state == "on"
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": main.entity_id}, blocking=True
            )
            command.assert_called_once_with(False, False, None)
            assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await hass.async_stop(force=True)
