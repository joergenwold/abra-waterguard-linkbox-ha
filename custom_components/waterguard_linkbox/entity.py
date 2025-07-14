"""Base entity for Waterguard Linkbox."""
from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import WaterguardDataUpdateCoordinator
from .const import FIRMWARE_VERSION


class WaterguardEntity(CoordinatorEntity[WaterguardDataUpdateCoordinator]):
    """Base class for Waterguard entities."""

    def __init__(self, coordinator: WaterguardDataUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(coordinator.domain, coordinator.hub.device_id)},
            "name": f"Waterguard Hub ({coordinator.hub.host})",
            "manufacturer": "Fell Tech",
            "model": "Abra Linkbox+ / Waterguard Hub",
            "sw_version": FIRMWARE_VERSION,
        } 