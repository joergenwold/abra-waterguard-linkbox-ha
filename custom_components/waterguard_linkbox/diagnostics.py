"""Diagnostics support for Waterguard Linkbox."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import WaterguardDataUpdateCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: WaterguardDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    return {
        "hub": {
            "host": coordinator.hub.host,
            "port": coordinator.hub.port,
            "device_id": coordinator.hub.device_id,
        },
        "data": coordinator.data,
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "last_update": coordinator.last_update_success_time.isoformat() if coordinator.last_update_success_time else None,
        "cache_stats": coordinator.get_cache_stats(),
        "state_machine": coordinator.get_state_machine_info(),
    } 