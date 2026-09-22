"""Local setup and reconfiguration without a Xiaomi account."""

import logging
import re

import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_MODEL, CONF_NAME, CONF_TOKEN
from homeassistant.helpers import selector
from miio import DeviceException

from .api import ERROR_HINTS, InvalidResponse, LampApi, UnsupportedModel, describe_error
from .const import DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


class EyeCareConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return await self._async_form("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._async_form("reconfigure", user_input)

    async def _async_form(self, step, user_input):
        errors = {}
        reason = ""
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
                api = LampApi(data[CONF_HOST], data[CONF_TOKEN])
                try:
                    info = await self.hass.async_add_executor_job(api.validate)
                except UnsupportedModel as err:
                    errors["base"] = "unsupported_model"
                    reason = f"reported model {err.model}"
                except InvalidResponse as err:
                    errors["base"] = "invalid_response"
                    reason = str(err)
                    _LOGGER.warning("Lamp at %s answered incompletely: %s", data[CONF_HOST], err)
                except (DeviceException, OSError) as err:
                    cause = describe_error(err)
                    errors["base"] = "cannot_connect"
                    reason = ERROR_HINTS[cause]
                    _LOGGER.warning(
                        "Cannot reach the lamp at %s: %s. %s",
                        data[CONF_HOST],
                        cause,
                        ERROR_HINTS[cause],
                    )
                    # A probe walks every property with its own timeout, so it can stall
                    # the form for a minute; only pay for it when debug logging is on.
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug("Setup probe: %s", await self._async_probe(api), exc_info=err)
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
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"reason": reason},
        )

    async def _async_probe(self, api: LampApi) -> dict:
        """Best-effort detail about a failed setup; never blocks saving a config."""
        try:
            return await self.hass.async_add_executor_job(api.probe)
        except Exception as err:  # noqa: BLE001 - diagnostics must not mask the real error
            # Exception text can echo the credentials that were passed in; keep the type only.
            return {"probe_failed": type(err).__name__}
