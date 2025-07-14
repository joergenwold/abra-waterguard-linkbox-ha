"""Switch platform for Waterguard Linkbox."""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_DESCRIPTIONS
from .coordinator import WaterguardDataUpdateCoordinator
from .entity import WaterguardEntity

_LOGGER = logging.getLogger(__name__)

SWITCH_DESCRIPTIONS = [
    SwitchEntityDescription(
        key="valve_control",
        name=ENTITY_DESCRIPTIONS["valve_control"]["name"],
        icon=ENTITY_DESCRIPTIONS["valve_control"]["icon"],
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    coordinator: WaterguardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    
    entities = [
        WaterguardSwitch(coordinator, description)
        for description in SWITCH_DESCRIPTIONS
    ]
    async_add_entities(entities)


class WaterguardSwitch(WaterguardEntity, SwitchEntity):
    """Representation of a switch."""

    def __init__(
        self,
        coordinator: WaterguardDataUpdateCoordinator,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.hub.device_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        if self.coordinator.data is None:
            return False
            
        # Get valve status from the valve section
        valve_data = self.coordinator.data.get("valve", {})
        valve_status = valve_data.get("valve_status1")
        
        # If no current data, try cache
        if valve_status is None and self.coordinator.has_cached_data("valve_valve_status1"):
            valve_status, _ = self.coordinator.get_entity_cache_reading("valve_valve_status1")
            _LOGGER.debug(f"Valve status: Using cached value: {valve_status}")
        
        # Handle different valve states
        if valve_status is None:
            return False
        elif valve_status == 3:
            return True  # Valve is open
        elif valve_status == 2:
            return False  # Valve is closed
        elif valve_status in [4, 1087]:
            # Valve is disconnected; we cannot know the state, so return False.
            _LOGGER.warning("Valve is disconnected - cannot determine state")
            return False
        else:
            # Unknown state - log and return False
            _LOGGER.debug(f"Unknown valve status: {valve_status}")
            return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.info("User requested valve open")
        
        # Log current valve state before command
        valve_data = self.coordinator.data.get("valve", {}) if self.coordinator.data else {}
        current_status = valve_data.get("valve_status1", "unknown")
        _LOGGER.info(f"Current valve status before open command: {current_status}")
        
        # Send command to hub
        success = await self.hass.async_add_executor_job(
            self.coordinator.hub.control_valve, "open"
        )
        
        if success:
            _LOGGER.info("Valve open command sent successfully")
            # Force immediate refresh to get updated state
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to send valve open command")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.info("User requested valve close")
        
        # Log current valve state before command
        valve_data = self.coordinator.data.get("valve", {}) if self.coordinator.data else {}
        current_status = valve_data.get("valve_status1", "unknown")
        _LOGGER.info(f"Current valve status before close command: {current_status}")
        
        # Send command to hub
        success = await self.hass.async_add_executor_job(
            self.coordinator.hub.control_valve, "close"
        )
        
        if success:
            _LOGGER.info("Valve close command sent successfully")
            # Force immediate refresh to get updated state
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to send valve close command")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self.coordinator.data is None:
            return False
        if not self.coordinator.last_update_success:
            if self.coordinator.data is not None:
                return True
            return False

        # Available only if we have coordinator data and valve system is functional
        if self.coordinator.data and "valve" in self.coordinator.data:
            valve_data = self.coordinator.data["valve"]
            num_valves = valve_data.get("num_valves")
            valve1_status = valve_data.get("valve_status1")
            
            # Check if valve system is disconnected
            if num_valves == 319:
                _LOGGER.debug("Valve system is disconnected (num_valves=319)")
                return False
            
            # Check if valve 1 is disconnected
            if valve1_status in [4, 1087]:
                _LOGGER.debug("Valve 1 is disconnected")
                return False
            
            # The switch is available if num_valves is a number >= 1 and valve is not disconnected
            return isinstance(num_valves, (int, float)) and num_valves >= 1
            
        return False 