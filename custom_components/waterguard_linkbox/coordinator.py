"""Data update coordinator for the Waterguard Linkbox integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta, datetime
from typing import Any, Optional, Dict, List, Tuple
from collections import OrderedDict
import threading

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_FAST_POLL_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_FAST_POLL_INTERVAL,
    CONF_POLL_WIRELESS,
    CONF_WIRELESS_POLL_INTERVAL,
    CONF_ENABLE_NOTIFICATIONS,
    DOMAIN,
    CONF_NOTIFICATION_PERSISTENT,
    CONF_NOTIFICATION_MOBILE,
)
from .hub import WaterguardLinkboxHub
from .notifications import NotificationManager

_LOGGER = logging.getLogger(__name__)


class EntityCache:
    """Entity-scoped cache with intelligent eviction."""
    
    def __init__(self, max_entries_per_entity: int = 10):
        """Initialize cache with configurable retention."""
        self._max_entries = max_entries_per_entity
        self._cache: Dict[str, OrderedDict] = {}
        self._lock = threading.RLock()
        
    def add_reading(self, entity_key: str, value: Any, timestamp: datetime) -> None:
        """Add a reading for a specific entity."""
        with self._lock:
            if entity_key not in self._cache:
                self._cache[entity_key] = OrderedDict()
            
            # Add new reading
            self._cache[entity_key][timestamp] = value
            
            # Evict oldest entries if we exceed max_entries, but always keep at least one
            if len(self._cache[entity_key]) > self._max_entries:
                # Remove oldest entries, keeping the most recent max_entries
                while len(self._cache[entity_key]) > self._max_entries:
                    self._cache[entity_key].popitem(last=False)
                    
    def get_latest_reading(self, entity_key: str) -> Tuple[Any, Optional[datetime]]:
        """Get the most recent reading for an entity."""
        with self._lock:
            if entity_key not in self._cache or not self._cache[entity_key]:
                return None, None
            
            # Get the most recent entry
            timestamp, value = self._cache[entity_key].popitem(last=True)
            # Put it back at the end (most recent)
            self._cache[entity_key][timestamp] = value
            return value, timestamp
    
    def get_all_readings(self, entity_key: str) -> List[Tuple[datetime, Any]]:
        """Get all readings for an entity (oldest first)."""
        with self._lock:
            if entity_key not in self._cache:
                return []
            return [(ts, val) for ts, val in self._cache[entity_key].items()]
    
    def has_entity_data(self, entity_key: str) -> bool:
        """Check if entity has any cached data."""
        with self._lock:
            return entity_key in self._cache and len(self._cache[entity_key]) > 0
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        with self._lock:
            return {entity: len(readings) for entity, readings in self._cache.items()}


class StateMachine:
    """State machine for alarm/valve synchronization."""
    
    def __init__(self):
        """Initialize state machine."""
        self._alarm_state = "normal"  # normal, active, resetting
        self._valve_state = "unknown"  # unknown, open, closed, disconnected
        self._last_alarm_timestamp = None
        self._last_valve_change_timestamp = None
        self._lock = threading.RLock()
        
    def update_alarm_state(self, alarm_active: bool, timestamp: datetime) -> bool:
        """Update alarm state and return True if state changed."""
        with self._lock:
            new_state = "active" if alarm_active else "normal"
            if new_state != self._alarm_state:
                self._alarm_state = new_state
                self._last_alarm_timestamp = timestamp
                _LOGGER.info(f"Alarm state changed to: {new_state}")
                return True
            return False
    
    def update_valve_state(self, valve_status: int, timestamp: datetime) -> bool:
        """Update valve state and return True if state changed."""
        with self._lock:
            # Map valve status to state
            if valve_status == 3:
                new_state = "open"
            elif valve_status == 2:
                new_state = "closed"
            elif valve_status in [4, 1087]:
                new_state = "disconnected"
            else:
                new_state = "unknown"
                
            if new_state != self._valve_state:
                self._valve_state = new_state
                self._last_valve_change_timestamp = timestamp
                _LOGGER.info(f"Valve state changed to: {new_state} (status: {valve_status})")
                return True
            return False
    
    def get_alarm_state(self) -> str:
        """Get current alarm state."""
        with self._lock:
            return self._alarm_state
    
    def get_valve_state(self) -> str:
        """Get current valve state."""
        with self._lock:
            return self._valve_state
    
    def should_force_valve_sync(self) -> bool:
        """Determine if we should force valve state synchronization."""
        with self._lock:
            # Force sync if alarm just cleared or valve state is uncertain
            return (self._alarm_state == "normal" and 
                   self._valve_state in ["unknown", "disconnected"])


class WaterguardDataUpdateCoordinator(DataUpdateCoordinator):
    """Data update coordinator for the Waterguard Linkbox integration."""

    def __init__(self, hass: HomeAssistant, hub: WaterguardLinkboxHub, entry: ConfigEntry, scan_interval: int, fast_poll_interval: int) -> None:
        """Initialize the data update coordinator."""
        self.hub = hub
        self.domain = DOMAIN
        # Get polling interval from config entry options, fallback to provided scan_interval
        poll_seconds = entry.options.get("scan_interval", scan_interval)
        self._scan_interval = timedelta(seconds=poll_seconds)
        self._fast_poll_interval = timedelta(seconds=fast_poll_interval)
        self._notification_manager: NotificationManager | None = None
        self._alarm_active = False
        self._wireless_discovery_complete = False
        
        # Initialize cache and state machine
        self._entity_cache = EntityCache(max_entries_per_entity=10)
        self._state_machine = StateMachine()
        
        # Store last known wireless sensor data to persist during sleep
        self._last_wireless_data: dict[str, Any] = {}
        self._previous_connected_valves = set()  # Track previously connected valve indices
        self._notified_disconnected_valves = set()  # Track which valves we've already notified about
        self._last_update_seconds: float | None = None
        # Wireless polling config
        self._poll_wireless: bool = entry.options.get(CONF_POLL_WIRELESS, True)
        self._wireless_poll_interval = timedelta(seconds=entry.options.get(CONF_WIRELESS_POLL_INTERVAL, 30))
        self._last_wireless_poll: datetime | None = None
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=self._scan_interval,
        )
        self.config_entry = entry

    async def async_setup_notifications(self) -> None:
        """Set up notification manager if enabled."""
        if self.config_entry.options.get(CONF_ENABLE_NOTIFICATIONS, True):
            self._notification_manager = NotificationManager(self.hass, self.hub)
            await self._notification_manager.async_setup()
            _LOGGER.info("Notification manager enabled")
        else:
            _LOGGER.info("Notifications disabled in configuration")

    async def async_discover_wireless_sensors(self) -> dict[str, Any]:
        """Discover wireless sensors during setup."""
        if self._wireless_discovery_complete:
            return getattr(self, '_discovered_wireless_sensors', {})
        _LOGGER.warning("🔍 STARTING wireless sensor discovery...")
        try:
            discovered_sensors = {}
            for attempt in range(3):
                _LOGGER.warning(f"🔍 Wireless discovery attempt {attempt + 1}/3")
                attempt_result = await self.hass.async_add_executor_job(
                    self.hub.discover_wireless_sensors
                )
                if attempt_result:
                    discovered_sensors.update(attempt_result)
                    _LOGGER.warning(f"✅ Discovery attempt {attempt + 1} found {len(attempt_result)} wireless sensors")
                    break
                else:
                    _LOGGER.warning(f"❌ Discovery attempt {attempt + 1} found no wireless sensors")
                    if attempt < 2:
                        await asyncio.sleep(2)
            if discovered_sensors:
                _LOGGER.warning(f"🎯 Initial discovery found {len(discovered_sensors)} wireless sensors total")
                self._discovered_wireless_sensors = discovered_sensors
                sensor_values = {}
                for sensor_key, sensor_info in discovered_sensors.items():
                    sensor_values[sensor_key] = sensor_info.get('value')
                self._last_wireless_data = sensor_values
                _LOGGER.warning(f"💾 Cached wireless data: {sensor_values}")
            else:
                _LOGGER.warning("⚠️ No wireless sensors found during initial discovery - they may be sleeping")
                self._discovered_wireless_sensors = {}
        except Exception as err:
            _LOGGER.warning("❌ Error during wireless sensor discovery: %s", err)
            self._discovered_wireless_sensors = {}
        finally:
            self._wireless_discovery_complete = True
            _LOGGER.warning("🏁 Wireless discovery process completed")
        return self._discovered_wireless_sensors

    def get_discovered_wireless_sensors(self) -> dict[str, Any]:
        """Get discovered wireless sensors."""
        return getattr(self, '_discovered_wireless_sensors', {})

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Waterguard hub."""
        try:
            start_time = datetime.now()
            # Decide whether to force a wireless read this cycle
            force_wireless = False
            if self._poll_wireless:
                # In alarm state: align with fast path (same cadence as hub polling)
                if self._alarm_active:
                    force_wireless = True
                else:
                    # Normal state: only once per configured interval
                    if (self._last_wireless_poll is None) or ((datetime.now() - self._last_wireless_poll) >= self._wireless_poll_interval):
                        force_wireless = True
            if not hasattr(self, '_update_count'):
                self._update_count = 0
            self._update_count += 1
            if force_wireless:
                _LOGGER.debug(f"Update {self._update_count} - forcing wireless sensor read (alarm_active: {self._alarm_active})")
            data = await self.hass.async_add_executor_job(
                self.hub.get_all_status, force_wireless
            )
            if force_wireless:
                self._last_wireless_poll = datetime.now()
            # Measure update duration for dynamic interval guarding
            try:
                self._last_update_seconds = max(0.0, (datetime.now() - start_time).total_seconds())
            except Exception:
                self._last_update_seconds = None
            if not data or not isinstance(data, dict):
                _LOGGER.debug("No valid data received from hub")
                if hasattr(self, 'data') and self.data is not None:
                    _LOGGER.debug("Using cached data due to empty response")
                    return self.data
                raise UpdateFailed("No valid data received from hub")
            # If wireless data is missing, use cached or empty dict
            if "wireless" not in data or not data["wireless"]:
                if self._last_wireless_data:
                    data["wireless"] = self._last_wireless_data.copy()
                    _LOGGER.debug("Using cached wireless data as fallback")
                else:
                    data["wireless"] = {}
                    _LOGGER.debug("No wireless data available, using empty dict")
            
            if data.get("valve") and "num_valves" in data["valve"]:
                raw_num_valves = data["valve"]["num_valves"]
                if raw_num_valves == 2:
                    data["valve"]["num_valves"] = 1
                elif raw_num_valves == 3:
                    data["valve"]["num_valves"] = 2
                elif raw_num_valves == 319:
                    data["valve"]["num_valves"] = 0
                _LOGGER.debug(f"Interpreted num_valves: raw={raw_num_valves} -> final={data['valve']['num_valves']}")
            
            current_time = datetime.now()
            data = await self._process_data_through_cache(data, current_time)
            await self._update_state_machine(data, current_time)
            await self._check_alarm_conditions(data)
            if self._notification_manager:
                await self._notification_manager.async_check_alarms(data, self._last_wireless_data)
            return data
        except Exception as err:
            _LOGGER.debug("Error fetching data from Waterguard hub: %s", err)
            if hasattr(self, 'data') and self.data is not None:
                if "timeout" in str(err).lower() or "connection" in str(err).lower():
                    _LOGGER.debug("Transient error detected, maintaining previous data")
                    if self._last_wireless_data and "wireless" not in self.data:
                        self.data["wireless"] = self._last_wireless_data.copy()
                    return self.data
            raise UpdateFailed(f"Error communicating with Waterguard hub: {err}") from err
    
    async def _process_data_through_cache(self, data: dict[str, Any], timestamp: datetime) -> dict[str, Any]:
        """Process incoming data through the entity cache system."""
        # Cache water sensor data
        water_data = data.get("water", {})
        for key, value in water_data.items():
            if value is not None:
                self._entity_cache.add_reading(f"water_{key}", value, timestamp)
        
        # Cache valve data
        valve_data = data.get("valve", {})
        for key, value in valve_data.items():
            if value is not None:
                self._entity_cache.add_reading(f"valve_{key}", value, timestamp)
        
        # Process wireless sensor data with fallback
        current_wireless = data.get("wireless", {})
        if current_wireless:
            # Update cache with new wireless data
            for key, value in current_wireless.items():
                if value is not None:
                    self._entity_cache.add_reading(f"wireless_{key}", value, timestamp)
            
            # Update legacy cache
            self._last_wireless_data.update(current_wireless)
            _LOGGER.debug(f"Updated wireless cache with {len(current_wireless)} sensors")
        else:
            # No current wireless data - use cached data from entity cache
            cached_wireless = {}
            for key in ["leak1", "leak2", "temperature", "humidity", "battery_voltage"]:
                value, _ = self._entity_cache.get_latest_reading(f"wireless_{key}")
                if value is not None:
                    cached_wireless[key] = value
            
            if cached_wireless:
                data["wireless"] = cached_wireless
                _LOGGER.debug(f"Using cached wireless data for {len(cached_wireless)} sensors (sensors sleeping)")
        
        return data

    async def _update_state_machine(self, data: dict[str, Any], timestamp: datetime) -> None:
        """Update state machine with current data."""
        # Update alarm state
        alarm_active = self._determine_alarm_state(data)
        alarm_state_changed = self._state_machine.update_alarm_state(alarm_active, timestamp)
        
        # Update valve state
        valve_data = data.get("valve", {})
        valve1_status = valve_data.get("valve_status1")
        valve2_status = valve_data.get("valve_status2")
        num_valves = valve_data.get("num_valves")
        # Track connected valves
        current_connected_valves = set()
        if num_valves is not None:
            if num_valves >= 1 and valve1_status not in [4, 1087, None]:
                current_connected_valves.add(1)
            if num_valves >= 2 and valve2_status not in [4, 1087, None]:
                current_connected_valves.add(2)
        # Detect disconnected valves
        if self._previous_connected_valves:
            disconnected = self._previous_connected_valves - current_connected_valves
            for idx in disconnected:
                if idx not in self._notified_disconnected_valves:
                    _LOGGER.debug(f"Valve {idx} transition from connected to unknown/disconnected observed; deferring to notification manager debounce")
                    self._notified_disconnected_valves.add(idx)
        # Remove from notified set if valve is reconnected
        for idx in current_connected_valves:
            if idx in self._notified_disconnected_valves:
                self._notified_disconnected_valves.remove(idx)
        self._previous_connected_valves = current_connected_valves.copy()
        valve_state_changed = self._state_machine.update_valve_state(valve1_status, timestamp)
        
        # Force valve sync if needed
        if self._state_machine.should_force_valve_sync():
            _LOGGER.info("Forcing valve state synchronization")
            await self._force_valve_sync()
        
        # Log state changes
        if alarm_state_changed:
            _LOGGER.info(f"Alarm state changed to: {self._state_machine.get_alarm_state()}")
        if valve_state_changed:
            _LOGGER.info(f"Valve state changed to: {self._state_machine.get_valve_state()}")

    def _determine_alarm_state(self, data: dict[str, Any]) -> bool:
        """Determine if any alarm is currently active."""
        water_data = data.get("water", {})
        valve_data = data.get("valve", {})
        wireless_data = data.get("wireless", {})
        
        alarm_active = False
        
        # Water alarms
        alarm_value = water_data.get("alarm")
        leak_value = water_data.get("leak1")
        
        if alarm_value is not None and alarm_value >= 1.0:
            alarm_active = True
            _LOGGER.warning("System alarm active: %s", alarm_value)
        
        if leak_value is not None and leak_value >= 1.0:
            alarm_active = True
            _LOGGER.warning("Water detected on sensor tape: %s", leak_value)
        
        # Wireless leak alarms (handle any number of leak channels: leak1, leak2, leak3, ...)
        for sensor_key, value in wireless_data.items():
            if isinstance(sensor_key, str) and sensor_key.startswith("leak") and value is not None:
                try:
                    if float(value) >= 1.0:
                        alarm_active = True
                        _LOGGER.warning("Wireless leak detected on %s: %s", sensor_key, value)
                except (TypeError, ValueError):
                    continue
        
        # Valve disconnection alarms
        num_valves = valve_data.get("num_valves")
        valve1_status = valve_data.get("valve_status1")
        valve2_status = valve_data.get("valve_status2")
        
        valve_system_disconnected = (num_valves == 319)
        valve1_disconnected = (valve1_status in [4, 1087])
        valve2_disconnected = (num_valves and num_valves >= 2 and valve2_status in [4, 1087])
        
        if valve_system_disconnected or valve1_disconnected or valve2_disconnected:
            alarm_active = True
            _LOGGER.warning(
                "Valve system issue detected: system_disconnected=%s, valve1_disconnected=%s, valve2_disconnected=%s",
                valve_system_disconnected, valve1_disconnected, valve2_disconnected
            )
        
        # Low battery alarm
        battery_voltage = wireless_data.get("battery_voltage")
        if battery_voltage is not None and battery_voltage < 2.5:
            alarm_active = True
            _LOGGER.warning("Low battery detected: %s", battery_voltage)
        
        return alarm_active

    async def _force_valve_sync(self) -> None:
        """Force synchronization of valve state with hub."""
        try:
            # Read current valve status from hub
            valve_status = await self.hass.async_add_executor_job(
                self.hub.read_valve_status
            )
            
            if valve_status and "valve_status1" in valve_status:
                current_status = valve_status["valve_status1"]
                _LOGGER.info(f"Forced valve sync - current status: {current_status}")
                
                # Update state machine with fresh data
                self._state_machine.update_valve_state(current_status, datetime.now())
                
        except Exception as err:
            _LOGGER.error(f"Error during forced valve sync: {err}")

    async def _check_alarm_conditions(self, data: dict[str, Any]) -> None:
        """Check for alarm conditions and adjust polling rate."""
        alarm_active = self._determine_alarm_state(data)

        # Base interval selection
        base_interval = self._fast_poll_interval if alarm_active else self._scan_interval

        # Dynamic guard: ensure interval is not shorter than processing time * 1.5 and at least 1s
        processing_guard = (self._last_update_seconds or 0.0) * 1.5
        safe_seconds = max(base_interval.total_seconds(), processing_guard, 1.0)
        target_interval = timedelta(seconds=safe_seconds)

        # Track state change for logging semantics
        if alarm_active != self._alarm_active:
            self._alarm_active = alarm_active
            state_label = "fast" if alarm_active else "normal"
            _LOGGER.info("Alarm %s - switching to %s polling (%.2fs)",
                        "detected" if alarm_active else "cleared",
                        state_label,
                        safe_seconds)

        # Apply interval if changed significantly (>0.2s)
        if not self.update_interval or abs(self.update_interval.total_seconds() - safe_seconds) > 0.2:
            self.update_interval = target_interval
            _LOGGER.debug("Polling interval set to %.2fs (processing_guard=%.2fs)", safe_seconds, processing_guard)
    
    def get_notification_manager(self) -> Optional[NotificationManager]:
        """Get the notification manager."""
        return self._notification_manager
    
    async def async_update_options(self) -> None:
        """Update coordinator options."""
        try:
            # Store current alarm state to preserve fast polling behavior
            was_alarm_active = self._alarm_active
            
            # Update scan interval with validation
            scan_interval = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            scan_interval = max(1, min(300, scan_interval))  # Clamp to reasonable range
            self._scan_interval = timedelta(seconds=scan_interval)
            
            # Update fast poll interval with validation
            fast_poll_interval = self.config_entry.options.get(CONF_FAST_POLL_INTERVAL, DEFAULT_FAST_POLL_INTERVAL)
            fast_poll_interval = max(1, min(10, fast_poll_interval))  # Clamp to reasonable range
            self._fast_poll_interval = timedelta(seconds=fast_poll_interval)
            
            # Update the actual polling interval based on current state
            if was_alarm_active:
                self.update_interval = self._fast_poll_interval
                _LOGGER.info("Options updated - using fast polling (%ds) due to active alarm", 
                            self.update_interval.total_seconds())
            else:
                # If no alarm, use the new scan interval
                self.update_interval = self._scan_interval
                _LOGGER.info("Options updated - using normal polling (%ds)", 
                            self.update_interval.total_seconds())
            
            # Update notification settings with error handling
            if self._notification_manager:
                try:
                    notification_settings = {
                        "persistent_notifications": self.config_entry.options.get(CONF_NOTIFICATION_PERSISTENT, True),
                        "mobile_notifications": self.config_entry.options.get(CONF_NOTIFICATION_MOBILE, True),
                    }
                    self._notification_manager.update_notification_settings(notification_settings)
                except Exception as err:
                    _LOGGER.warning("Failed to update notification settings: %s", err)
                    
            _LOGGER.info("Coordinator options updated - scan_interval=%ds, fast_poll_interval=%ds", 
                        scan_interval, fast_poll_interval)
                        
        except Exception as err:
            _LOGGER.error("Error updating coordinator options: %s", err)

    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics for diagnostics."""
        return self._entity_cache.get_cache_stats()
    
    def get_state_machine_info(self) -> Dict[str, Any]:
        """Get state machine information for diagnostics."""
        return {
            "alarm_state": self._state_machine.get_alarm_state(),
            "valve_state": self._state_machine.get_valve_state(),
            "should_force_sync": self._state_machine.should_force_valve_sync(),
        }
    
    def get_entity_cache_reading(self, entity_key: str) -> Tuple[Any, Optional[datetime]]:
        """Get the latest reading for a specific entity from cache."""
        return self._entity_cache.get_latest_reading(entity_key)
    
    def has_cached_data(self, entity_key: str) -> bool:
        """Check if an entity has cached data."""
        return self._entity_cache.has_entity_data(entity_key) 