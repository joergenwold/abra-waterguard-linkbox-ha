"""Binary sensor platform for Waterguard Water System."""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, 
    BINARY_SENSOR_DESCRIPTIONS,
)
from .coordinator import WaterguardDataUpdateCoordinator
from .entity import WaterguardEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Waterguard binary sensor platform."""
    coordinator: WaterguardDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    discovered_sensors: dict[str, Any] = coordinator.get_discovered_wireless_sensors()
    
    entities = []
    
    # Create main (non-wireless) binary sensors
    main_descriptions = [
        desc for desc in BINARY_SENSOR_DESCRIPTIONS if not desc.key.startswith("wireless_")
    ]
    for description in main_descriptions:
        entities.append(WaterguardBinarySensor(coordinator, description))

    # Get descriptions for possible wireless sensors
    wireless_descriptions = {
        desc.key: desc for desc in BINARY_SENSOR_DESCRIPTIONS if desc.key.startswith("wireless_")
    }

    # Create entities only for discovered wireless leak sensors
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
            
            # If description exists, use it; otherwise, dynamically create for additional leak channels
            if entity_key in wireless_descriptions:
                _LOGGER.info(f"Setting up wireless binary sensor: {entity_key} (from discovered {sensor_key})")
                description = wireless_descriptions[entity_key]
                entities.append(WaterguardBinarySensor(coordinator, description))
            else:
                # Dynamically support additional leak channels: leak3, leak4, ...
                if entity_key.startswith("wireless_leak"):
                    _LOGGER.info(f"Dynamically adding wireless leak binary sensor: {entity_key}")
                    from homeassistant.components.binary_sensor import BinarySensorEntityDescription, BinarySensorDeviceClass
                    description = BinarySensorEntityDescription(
                        key=entity_key,
                        name=f"Waterguard Wireless Sensor {mapped_key}",
                        device_class=BinarySensorDeviceClass.MOISTURE,
                    )
                    entities.append(WaterguardBinarySensor(coordinator, description))
    
    async_add_entities(entities)


class WaterguardBinarySensor(WaterguardEntity, BinarySensorEntity):
    """Waterguard binary sensor."""

    def __init__(
        self,
        coordinator: WaterguardDataUpdateCoordinator,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.hub.host}_{coordinator.hub.device_id}_{description.key}"
        # Store last known state for wireless sensors to persist during sleep
        self._last_known_state: Optional[bool] = None

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if the binary sensor is on."""
        data = self.coordinator.data
        if data is None:
            # For wireless sensors, return last known state if coordinator data is None
            if self.entity_description.key.startswith("wireless_"):
                return self._last_known_state
            return None
            
        key_mapping = {
            "water_alarm": ("water", "alarm", "water_alarm"),
            "water_leak1": ("water", "leak1", "water_leak1"),
            "valve_disconnected": ("valve", "valve_status1", "valve_valve_status1"),
            "hub_connected": (None, None, None),
            "wireless_leak1": ("wireless", "leak1", "wireless_leak1"),
            "wireless_leak2": ("wireless", "leak2", "wireless_leak2"),
        }
        
        if self.entity_description.key in key_mapping:
            section, data_key, cache_key = key_mapping[self.entity_description.key]
            
            if self.entity_description.key == "hub_connected":
                return self.coordinator.last_update_success

            value = data.get(section, {}).get(data_key)
            
            # Fallback to cache if no current data is available
            if value is None and self.coordinator.has_cached_data(cache_key):
                value, _ = self.coordinator.get_entity_cache_reading(cache_key)
                _LOGGER.debug(f"{self.entity_description.key}: Using cached value: {value}")
            
            if self.entity_description.key in ["water_alarm", "water_leak1"]:
                if value is None:
                    # Sensor is not connected; report as unknown
                    _LOGGER.debug(f"{self.entity_description.key}: No data - sensor not connected, reporting as unknown")
                    return None
                
                # If we have actual data, parse it with validation
                try:
                    float_value = float(value)
                    if not (0 <= float_value <= 10):  # Reasonable range for alarm values
                        _LOGGER.warning(f"{self.entity_description.key}: Value {float_value} outside expected range, treating as normal")
                        return False
                        
                    _LOGGER.debug(f"{self.entity_description.key}: Processing value {float_value}")
                    # Any non-zero value indicates an alarm or leak
                    if float_value >= 1.0:
                        _LOGGER.info(f"{self.entity_description.key}: ALARM/LEAK DETECTED - value {float_value}")
                        return True
                    elif float_value > 0.0:
                        _LOGGER.debug(f"{self.entity_description.key}: Intermediate value {float_value} - treating as alarm")
                        return True
                    else:
                        _LOGGER.debug(f"{self.entity_description.key}: Normal/dry - value {float_value}")
                        return False
                except (ValueError, TypeError, OverflowError) as err:
                    _LOGGER.warning(f"{self.entity_description.key}: Could not parse value {value} ({err}), assuming normal/dry")
                    return False
            
            # Valve disconnection logic
            elif self.entity_description.key == "valve_disconnected":
                valve_data = data.get("valve", {})
                num_valves = valve_data.get("num_valves")
                valve1_status = valve_data.get("valve_status1")
                valve2_status = valve_data.get("valve_status2")

                # A value of 319 means the whole valve system is disconnected
                if num_valves == 319:
                    return True
                
                # Check status of valve 1 (if it exists)
                if valve1_status in [4, 1087]:
                    return True
                
                # Check status of valve 2 only if 2 or more valves are present
                if num_valves and num_valves >= 2 and valve2_status in [4, 1087]:
                    return True
                
                return False
            
            # Handle wireless sensors 
            elif self.entity_description.key.startswith("wireless_"):
                if value is None:
                    # For wireless sensors, return last known state when current data is None
                    return self._last_known_state
                    
                try:
                    float_value = float(value)
                    
                    # Validate range to prevent false alarms from corrupted data
                    if self.entity_description.key.startswith("wireless_leak"):
                        if not (0 <= float_value <= 2):  # Reasonable range for leak sensors
                            _LOGGER.warning(f"{self.entity_description.key}: Value {float_value} outside expected range, treating as dry")
                            current_state = False
                        elif float_value >= 1.0:
                            current_state = True  # Water detected
                        else:
                            # For other values, assume dry state
                            current_state = False
                    else:
                        # For non-leak wireless sensors, use simple boolean logic
                        current_state = float_value >= 1.0
                    
                    # Store last known state for wireless sensors
                    self._last_known_state = current_state
                    return current_state
                except (ValueError, TypeError, OverflowError) as err:
                    _LOGGER.warning(f"{self.entity_description.key}: Could not parse value {value} ({err}), using last known state")
                    # If value cannot be converted to float, return last known state for wireless
                    return self._last_known_state
            
            # Generic fallback
            else:
                if value is None:
                    return None
                try:
                    float_value = float(value)
                    return float_value == 1.0
                except (ValueError, TypeError):
                    return None
            
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self.coordinator.data is None:
            return False
        # Allow for transient failures - only mark unavailable after multiple consecutive failures
        if not self.coordinator.last_update_success:
            # If we have valid data, allow for temporary connection issues
            if self.coordinator.data is not None:
                return True
            return False
            
        # For water sensors, they are available if we have coordinator data
        # and a specific value for the sensor.
        if self.entity_description.key in ["water_alarm", "water_leak1"]:
            water_data = self.coordinator.data.get("water", {})
            key_map = {
                "water_alarm": "alarm",
                "water_leak1": "leak1",
            }
            data_key = key_map.get(self.entity_description.key)
            if not data_key:
                return False
            return water_data.get(data_key) is not None
            
        # For valve disconnection sensor, it's available if we have valve data
        if self.entity_description.key == "valve_disconnected":
            valve_data = self.coordinator.data.get("valve", {})
            return self.coordinator.data is not None and "num_valves" in valve_data
            
        # Special handling for wireless sensors - they are intermittent
        if self.entity_description.key.startswith("wireless_"):
            # Wireless sensors are available if:
            # 1. The coordinator is working, AND
            # 2. We have either current data OR a cached last known state
            coordinator_ok = self.coordinator.data is not None
            wireless_data = self.coordinator.data.get("wireless", {}) if self.coordinator.data else {}
            sensor_key = self.entity_description.key.replace("wireless_", "")
            has_current_data = wireless_data.get(sensor_key) is not None
            has_cached_state = self._last_known_state is not None
            
            return coordinator_ok and (has_current_data or has_cached_state)
            
        return self.coordinator.data is not None 