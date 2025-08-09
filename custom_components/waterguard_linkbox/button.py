"""Button entities for Waterguard Linkbox."""
from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, ENTITY_DESCRIPTIONS, RESET_VALUES
from .coordinator import WaterguardDataUpdateCoordinator
from .entity import WaterguardEntity

_LOGGER = logging.getLogger(__name__)

ENTITY_DESCRIPTIONS: Final[tuple[ButtonEntityDescription, ...]] = (
    ButtonEntityDescription(
        key="reset_leak",
        name="Reset Water Alarm",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    coordinator: WaterguardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    entities = [
        WaterguardResetButton(coordinator, description)
        for description in ENTITY_DESCRIPTIONS
    ]
    async_add_entities(entities)


class WaterguardResetButton(WaterguardEntity, ButtonEntity):
    """Reset button for water leak alarm."""

    def __init__(
        self,
        coordinator: WaterguardDataUpdateCoordinator,
        description: ButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.hub.device_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Reset water alarm button pressed")
        reset_value = RESET_VALUES.get("water_leak", 2)
        try:
            success = await self.hass.async_add_executor_job(
                self.coordinator.hub.reset_water_alarm, reset_value
            )
            if success:
                _LOGGER.info("Water alarm reset command sent successfully")
                self._attr_extra_state_attributes = {
                    **(self.extra_state_attributes or {}),
                    "last_pressed": self.coordinator.hass.helpers.event.utcnow().isoformat(),
                    "last_result": "success",
                }
                
                # Force immediate refresh to get updated state
                await self.coordinator.async_request_refresh()
                
                # Log state machine info for debugging
                state_info = self.coordinator.get_state_machine_info()
                _LOGGER.info(f"State machine after reset: {state_info}")
                
            else:
                _LOGGER.error("Failed to reset water alarm")
                self._attr_extra_state_attributes = {
                    **(self.extra_state_attributes or {}),
                    "last_pressed": self.coordinator.hass.helpers.event.utcnow().isoformat(),
                    "last_result": "error",
                }
        except Exception as err:
            _LOGGER.error("Error resetting water alarm: %s", err)
            self._attr_extra_state_attributes = {
                **(self.extra_state_attributes or {}),
                "last_pressed": self.coordinator.hass.helpers.event.utcnow().isoformat(),
                "last_result": f"exception: {err}",
            }
    
    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success 