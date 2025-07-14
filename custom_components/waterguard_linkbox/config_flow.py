"""Config flow for Waterguard Linkbox integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_DEVICE_ID,
    CONF_SCAN_INTERVAL,
    CONF_FAST_POLL_INTERVAL,
    CONF_ENABLE_NOTIFICATIONS,
    CONF_NOTIFICATION_MOBILE,
    CONF_NOTIFICATION_PERSISTENT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_FAST_POLL_INTERVAL,
    DEFAULT_ENABLE_NOTIFICATIONS,
    DEFAULT_NOTIFICATION_MOBILE,
    DEFAULT_NOTIFICATION_PERSISTENT,
    DOMAIN,
)
from .hub import WaterguardLinkboxHub
from .discovery import async_discover_hubs, DiscoveryTimeout

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_DEVICE_ID): int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    hub = WaterguardLinkboxHub(data[CONF_HOST], data[CONF_PORT], data[CONF_DEVICE_ID])

    try:
        await hass.async_add_executor_job(hub.test_connection)
    except Exception as err:
        _LOGGER.error("Failed to connect to Waterguard hub: %s", err)
        raise CannotConnect from err

    # Get device info for the title
    try:
        device_info = await hass.async_add_executor_job(hub.get_device_info)
        title = f"Waterguard Hub ({data[CONF_HOST]})"
    except Exception:
        title = f"Waterguard Hub ({data[CONF_HOST]})"

    return {"title": title}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Waterguard Linkbox."""

    VERSION = 1
    
    def __init__(self):
        """Initialize the config flow."""
        self._discovered_hubs = []

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return OptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Create unique ID based on host and device ID
                unique_id = f"{user_input[CONF_HOST]}_{user_input[CONF_DEVICE_ID]}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(title=info["title"], data=user_input)

        # Try to discover hubs on the network
        try:
            self._discovered_hubs = await async_discover_hubs(self.hass)
        except DiscoveryTimeout:
            errors["base"] = "discovery_timeout"
        except Exception:
            _LOGGER.exception("Unknown error during discovery")
            errors["base"] = "discovery_error"
        
        if self._discovered_hubs:
            return await self.async_step_discovery()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "default_port": DEFAULT_PORT,
                "example_host": "192.168.1.100",
                "example_device_id": "2229704",
            },
        )
        
    async def async_step_discovery(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle discovery step. User can select a discovered hub or choose manual entry."""
        if user_input is not None:
            if user_input.get("manual_entry"):
                return await self.async_step_user()
            
            selected_host = user_input["discovered_hub"]
            selected_hub = next((hub for hub in self._discovered_hubs if hub["host"] == selected_host), None)

            if selected_hub:
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({
                        vol.Required(CONF_HOST, default=selected_hub["host"]): str,
                        vol.Optional(CONF_PORT, default=selected_hub["port"]): int,
                        vol.Required(CONF_DEVICE_ID, default=selected_hub.get("device_id")): int,
                    }),
                    description_placeholders={
                        "default_port": selected_hub["port"],
                        "example_host": "192.168.1.100",
                        "example_device_id": "2229704",
                    },
                )

        # Show list of discovered hubs
        discovered_hubs_options = {hub["host"]: f"{hub['host']}:{hub['port']}" for hub in self._discovered_hubs}

        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema({
                vol.Required("discovered_hub"): vol.In(discovered_hubs_options),
                vol.Optional("manual_entry", default=False): bool,
            }),
            description_placeholders={"discovery_info": "Select a discovered hub or choose manual entry below."},
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Waterguard Linkbox."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options flow."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
                vol.Optional(
                    CONF_FAST_POLL_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_FAST_POLL_INTERVAL, DEFAULT_FAST_POLL_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                vol.Optional(
                    CONF_ENABLE_NOTIFICATIONS,
                    default=self.config_entry.options.get(
                        CONF_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS
                    ),
                ): bool,
                vol.Optional(
                    CONF_NOTIFICATION_MOBILE,
                    default=self.config_entry.options.get(
                        CONF_NOTIFICATION_MOBILE, DEFAULT_NOTIFICATION_MOBILE
                    ),
                ): bool,
                vol.Optional(
                    CONF_NOTIFICATION_PERSISTENT,
                    default=self.config_entry.options.get(
                        CONF_NOTIFICATION_PERSISTENT, DEFAULT_NOTIFICATION_PERSISTENT
                    ),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            description_placeholders={
                "scan_interval_desc": "How often to poll the hub for data (1-300 seconds)",
                "notifications_desc": "Enable alarm notifications",
            },
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth.""" 