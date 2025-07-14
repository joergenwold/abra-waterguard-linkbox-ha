"""The Waterguard Linkbox integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_DEVICE_ID, DOMAIN, CONF_SCAN_INTERVAL, CONF_FAST_POLL_INTERVAL, DEFAULT_SCAN_INTERVAL, DEFAULT_FAST_POLL_INTERVAL
from .hub import WaterguardLinkboxHub
from .coordinator import WaterguardDataUpdateCoordinator
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Waterguard Linkbox from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, 47808)
    device_id = entry.data[CONF_DEVICE_ID]

    hub = WaterguardLinkboxHub(host, port, device_id)
    
    # Test connection
    try:
        await hass.async_add_executor_job(hub.test_connection)
    except Exception as err:
        _LOGGER.error("Failed to connect to Waterguard hub at %s: %s", host, err)
        raise ConfigEntryNotReady(f"Failed to connect to Waterguard hub: {err}") from err

    coordinator = WaterguardDataUpdateCoordinator(
        hass,
        hub,
        entry,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        entry.options.get(CONF_FAST_POLL_INTERVAL, DEFAULT_FAST_POLL_INTERVAL),
    )
    
    # Store coordinator in hass.data immediately
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Set up notifications
    await coordinator.async_setup_notifications()
    
    # Discover wireless sensors. The coordinator will cache the results internally.
    await coordinator.async_discover_wireless_sensors()
    
    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Set up services (only once)
    if not hass.services.has_service(DOMAIN, "test_notification"):
        await async_setup_services(hass)
    
    # Set up options update listener that updates coordinator directly instead of reloading
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options without reloading the entire integration."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_update_options()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Unload services if this is the last entry
        if not hass.data[DOMAIN]:
            await async_unload_services(hass)

    return unload_ok 