"""Local setup and reconfiguration without a Xiaomi account."""

import re

import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_MODEL, CONF_NAME, CONF_TOKEN
from homeassistant.helpers import selector
from miio import DeviceException

from .api import InvalidResponse, LampApi, UnsupportedModel
from .const import DOMAIN, NAME


class EyeCareConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return await self._async_form("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._async_form("reconfigure", user_input)

    async def _async_form(self, step, user_input):
        errors = {}
        entry = self._get_reconfigure_entry() if step == "reconfigure" else None
        defaults = entry.data if entry else {}
        if user_input is not None:
            data = dict(user_input)
            data[CONF_HOST] = data[CONF_HOST].strip()
            data[CONF_NAME] = data.get(CONF_NAME, NAME).strip() or NAME
            token = data.get(CONF_TOKEN, "").strip()
            if not token and entry:
                token = entry.data[CONF_TOKEN]
            data[CONF_TOKEN] = token.lower()
            if not re.fullmatch(r"[0-9a-fA-F]{32}", token):
                errors[CONF_TOKEN] = "invalid_token"
            elif not data[CONF_HOST] or "://" in data[CONF_HOST] or "/" in data[CONF_HOST]:
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    api = LampApi(data[CONF_HOST], data[CONF_TOKEN])
                    info = await self.hass.async_add_executor_job(api.validate)
                except UnsupportedModel:
                    errors["base"] = "unsupported_model"
                except InvalidResponse:
                    errors["base"] = "invalid_response"
                except DeviceException, OSError:
                    errors["base"] = "cannot_connect"
                else:
                    if entry and entry.unique_id != info.mac:
                        errors["base"] = "wrong_device"
                    else:
                        await self.async_set_unique_id(info.mac)
                        data.update({CONF_MODEL: info.model, "firmware": info.firmware})
                        if entry:
                            return self.async_update_reload_and_abort(entry, data_updates=data)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(title=data[CONF_NAME], data=data)
            defaults = data

        fields = {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, NAME)): str,
        }
        token_key = vol.Optional(CONF_TOKEN) if entry else vol.Required(CONF_TOKEN)
        # Never echo a stored token back into a form or translation placeholder.
        fields[token_key] = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )
        return self.async_show_form(step_id=step, data_schema=vol.Schema(fields), errors=errors)
