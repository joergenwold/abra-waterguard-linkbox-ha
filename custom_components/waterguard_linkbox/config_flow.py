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
from .discovery import async_discover_device_ids, DiscoveryTimeout

_LOGGER = logging.getLogger(__name__)

STEP_HOST_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)

STEP_DEVICE_ID_SCHEMA = vol.Schema(
    {
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
        self._discovered_device_ids = []
        self._host = None
        self._port = None

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return OptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - ask for host and port."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            
            # Try to discover device IDs for this specific host
            try:
                self._discovered_device_ids = await async_discover_device_ids(
                    self.hass, self._host, self._port
                )
            except DiscoveryTimeout:
                _LOGGER.info(f"No device IDs discovered for {self._host}")
                self._discovered_device_ids = []
            except Exception:
                _LOGGER.exception("Error during device ID discovery")
                self._discovered_device_ids = []
            
            # Move to device ID selection/input step
            return await self.async_step_device_id()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_HOST_SCHEMA,
            errors=errors,
            description_placeholders={
                "default_port": DEFAULT_PORT,
                "example_host": "192.168.1.100",
            },
        )
        
    async def async_step_device_id(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device ID selection or manual input."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            if user_input.get("manual_entry"):
                # User chose to enter device ID manually
                return self.async_show_form(
                    step_id="manual_device_id",
                    data_schema=STEP_DEVICE_ID_SCHEMA,
                    errors=errors,
                    description_placeholders={
                        "example_device_id": "2229704",
                    },
                )
            
            # User selected a discovered device ID
            selected_device_id = int(user_input["discovered_device_id"])
            return await self._create_entry(selected_device_id)

        # Show discovered device IDs if any were found
        if self._discovered_device_ids:
            discovered_options = {
                str(device_id): f"Device ID: {device_id}" 
                for device_id in self._discovered_device_ids
            }
            
            return self.async_show_form(
                step_id="device_id",
                data_schema=vol.Schema({
                    vol.Required("discovered_device_id"): vol.In(discovered_options),
                    vol.Optional("manual_entry", default=False): bool,
                }),
                description_placeholders={
                    "host": self._host,
                    "discovery_info": f"Found {len(self._discovered_device_ids)} device ID(s) for {self._host}. Select one or choose manual entry below."
                },
            )
        else:
            # No device IDs discovered, go directly to manual input
            return self.async_show_form(
                step_id="manual_device_id",
                data_schema=STEP_DEVICE_ID_SCHEMA,
                errors=errors,
                description_placeholders={
                    "host": self._host,
                    "example_device_id": "2229704",
                },
            )

    async def async_step_manual_device_id(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual device ID input."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            return await self._create_entry(device_id)

        return self.async_show_form(
            step_id="manual_device_id",
            data_schema=STEP_DEVICE_ID_SCHEMA,
            errors=errors,
            description_placeholders={
                "host": self._host,
                "example_device_id": "2229704",
            },
        )

    async def _create_entry(self, device_id: int) -> FlowResult:
        """Create the config entry."""
        user_input = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_DEVICE_ID: device_id,
        }
        
        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            return self.async_show_form(
                step_id="manual_device_id",
                data_schema=STEP_DEVICE_ID_SCHEMA,
                errors={"base": "cannot_connect"},
                description_placeholders={
                    "host": self._host,
                    "example_device_id": "2229704",
                },
            )
        except InvalidAuth:
            return self.async_show_form(
                step_id="manual_device_id",
                data_schema=STEP_DEVICE_ID_SCHEMA,
                errors={"base": "invalid_auth"},
                description_placeholders={
                    "host": self._host,
                    "example_device_id": "2229704",
                },
            )
        except Exception:
            _LOGGER.exception("Unexpected exception")
            return self.async_show_form(
                step_id="manual_device_id",
                data_schema=STEP_DEVICE_ID_SCHEMA,
                errors={"base": "unknown"},
                description_placeholders={
                    "host": self._host,
                    "example_device_id": "2229704",
                },
            )
        
        # Create unique ID based on host and device ID
        unique_id = f"{self._host}_{device_id}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        
        return self.async_create_entry(title=info["title"], data=user_input)


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