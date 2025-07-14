"""Sensor platform for Waterguard Water System."""
from __future__ import annotations

import logging
from typing import Any, Optional, Union

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_DESCRIPTIONS, STATE_MAPPINGS
from .coordinator import WaterguardDataUpdateCoordinator
from .entity import WaterguardEntity

_LOGGER = logging.getLogger(__name__)

SENSOR_DESCRIPTIONS = [
    SensorEntityDescription(
        key="num_valves",
        name=ENTITY_DESCRIPTIONS["num_valves"]["name"],
        icon=ENTITY_DESCRIPTIONS["num_valves"]["icon"],
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="valve_status1",
        name=ENTITY_DESCRIPTIONS["valve_status1"]["name"],
        icon=ENTITY_DESCRIPTIONS["valve_status1"]["icon"],
    ),
    SensorEntityDescription(
        key="valve_status2",
        name=ENTITY_DESCRIPTIONS["valve_status2"]["name"],
        icon=ENTITY_DESCRIPTIONS["valve_status2"]["icon"],
    ),
    SensorEntityDescription(
        key="wireless_temperature",
        name=ENTITY_DESCRIPTIONS["wireless_temperature"]["name"],
        icon=ENTITY_DESCRIPTIONS["wireless_temperature"]["icon"],
        device_class=ENTITY_DESCRIPTIONS["wireless_temperature"]["device_class"],
        native_unit_of_measurement=ENTITY_DESCRIPTIONS["wireless_temperature"]["unit"],
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="wireless_humidity",
        name=ENTITY_DESCRIPTIONS["wireless_humidity"]["name"],
        icon=ENTITY_DESCRIPTIONS["wireless_humidity"]["icon"],
        device_class=ENTITY_DESCRIPTIONS["wireless_humidity"]["device_class"],
        native_unit_of_measurement=ENTITY_DESCRIPTIONS["wireless_humidity"]["unit"],
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="wireless_battery",
        name=ENTITY_DESCRIPTIONS["wireless_battery"]["name"],
        icon=ENTITY_DESCRIPTIONS["wireless_battery"]["icon"],
        device_class=ENTITY_DESCRIPTIONS["wireless_battery"]["device_class"],
        native_unit_of_measurement=ENTITY_DESCRIPTIONS["wireless_battery"]["unit"],
        state_class=SensorStateClass.MEASUREMENT,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator: WaterguardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    discovered_sensors: dict[str, Any] = coordinator.get_discovered_wireless_sensors()

    entities = []

    # Create main (non-wireless) sensors
    main_descriptions = [
        desc for desc in SENSOR_DESCRIPTIONS if not desc.key.startswith("wireless_")
    ]
    for description in main_descriptions:
        entities.append(WaterguardSensor(coordinator, description))

    # Get descriptions for possible wireless sensors
    wireless_descriptions = {
        desc.key: desc for desc in SENSOR_DESCRIPTIONS if desc.key.startswith("wireless_")
    }
    
    # Create entities only for discovered wireless sensors
    if discovered_sensors:
        for sensor_key in discovered_sensors:
            # Map sensor discovery keys to entity description keys
            sensor_key_mapping = {
                "battery_voltage": "battery",  # battery_voltage sensor creates wireless_battery entity
                "leak1": "leak1",
                "leak2": "leak2", 
                "temperature": "temperature",
                "humidity": "humidity",
            }
            
            # Get the mapped key for entity creation
            mapped_key = sensor_key_mapping.get(sensor_key, sensor_key)
            entity_key = f"wireless_{mapped_key}"
            
            if entity_key in wireless_descriptions:
                _LOGGER.info(f"Setting up wireless sensor: {entity_key} (from discovered {sensor_key})")
                description = wireless_descriptions[entity_key]
                entities.append(WaterguardSensor(coordinator, description))

    async_add_entities(entities)


class WaterguardSensor(WaterguardEntity, SensorEntity):
    """Representation of a sensor."""

    def __init__(
        self,
        coordinator: WaterguardDataUpdateCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.hub.device_id}_{description.key}"
        # Store last known value for wireless sensors to persist during sleep
        self._last_known_value: Optional[str] = None
        # Store last stable state for valve sensors to determine opening/closing state
        if self.entity_description.key in ["valve_status1", "valve_status2"]:
            self._last_stable_valve_state: Optional[str] = None

    @property
    def native_value(self) -> str | None:
        """Return the native value of the sensor."""
        if self.coordinator.data is None:
            # For wireless sensors, return last known value if coordinator data is None
            if self.entity_description.key.startswith("wireless_"):
                return self._last_known_value
            return None
            
        # Map entity keys to data paths
        key_mapping = {
            "num_valves": ("valve", "num_valves"),
            "valve_status1": ("valve", "valve_status1"),
            "valve_status2": ("valve", "valve_status2"),
            "wireless_temperature": ("wireless", "temperature"),
            "wireless_humidity": ("wireless", "humidity"),
            "wireless_battery": ("wireless", "battery"),
        }
        
        if self.entity_description.key in key_mapping:
            section, data_key = key_mapping[self.entity_description.key]
            value = self.coordinator.data.get(section, {}).get(data_key)
        else:
            value = None
            
        if value is None:
            # For wireless sensors, return last known value when current data is None
            if self.entity_description.key.startswith("wireless_"):
                return self._last_known_value
            return None
            
        if self.entity_description.key == "num_valves":
            return str(value) if value is not None else None

        # Apply state mappings for valve-related sensors
        processed_value = None
        if self.entity_description.key in ["valve_status1", "valve_status2"] and "valve_status" in STATE_MAPPINGS:
            if value == 319.0:
                if self._last_stable_valve_state == "closed":
                    processed_value = "opening"
                elif self._last_stable_valve_state == "open":
                    processed_value = "closing"
                else:
                    processed_value = "moving"
            else:
                processed_value = STATE_MAPPINGS["valve_status"].get(value, str(value))
                if processed_value in ["open", "closed"]:
                    self._last_stable_valve_state = processed_value
        elif self.entity_description.key == "wireless_battery":

            if isinstance(value, (int, float)):
                processed_value = value
            else:
                processed_value = None
        else:
            processed_value = str(value) if value is not None else None
        
        # Store last known value for wireless sensors
        if self.entity_description.key.startswith("wireless_") and processed_value is not None:
            self._last_known_value = processed_value
            
        return processed_value

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self.coordinator.data is None:
            return False
        # Allow for transient failures, only mark unavailable after multiple consecutive failures
        if not self.coordinator.last_update_success:
            # If we have valid data, allow for temporary connection issues
            if self.coordinator.data is not None:
                return True
            return False
            
        # Check if the specific data is available
        key_mapping = {
            "num_valves": ("valve", "num_valves"),
            "valve_status1": ("valve", "valve_status1"),
            "valve_status2": ("valve", "valve_status2"),
            "wireless_temperature": ("wireless", "temperature"),
            "wireless_humidity": ("wireless", "humidity"),
            "wireless_battery": ("wireless", "battery"),
        }
        
        if self.entity_description.key in key_mapping:
            section, data_key = key_mapping[self.entity_description.key]

            # Availability for valve_status2
            if data_key == "valve_status2":
                valve_data = self.coordinator.data.get("valve", {})
                num_valves = valve_data.get("num_valves")
                return num_valves is not None and num_valves >= 2

            if self.entity_description.key.startswith("wireless_"):
                # Wireless sensors are available if:
                # 1. The coordinator is working, AND
                # 2. We have either current data OR a cached last known value
                coordinator_ok = self.coordinator.data is not None
                has_current_data = self.coordinator.data.get(section, {}).get(data_key) is not None
                has_cached_value = self._last_known_value is not None
                
                return coordinator_ok and (has_current_data or has_cached_value)
            
            # For non-wireless sensors, check if data is available
            section_data = self.coordinator.data.get(section, {})
            if isinstance(section_data, dict):
                return data_key in section_data or len(section_data) > 0
            
        return self.coordinator.data is not None 