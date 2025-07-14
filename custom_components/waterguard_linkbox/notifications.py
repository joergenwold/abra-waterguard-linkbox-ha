"""Notification manager for Waterguard Linkbox."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.components.persistent_notification import create
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.dt import utcnow

from .const import DOMAIN
from .hub import WaterguardLinkboxHub

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_IDS = {
    "water_alarm": f"{DOMAIN}_water_alarm",
    "water_leak": f"{DOMAIN}_water_leak",
    "valve_alarm": f"{DOMAIN}_valve_alarm",
    "low_battery": f"{DOMAIN}_low_battery",
    "connection_lost": f"{DOMAIN}_connection_lost",
    "wireless_leak_1": f"{DOMAIN}_wireless_leak_1",
    "wireless_leak_2": f"{DOMAIN}_wireless_leak_2",
}

ALARM_PRIORITIES = {
    "water_alarm": "high",
    "water_leak": "high", 
    "valve_alarm": "medium",
    "low_battery": "low",
    "connection_lost": "medium",
    "wireless_leak_1": "high",
    "wireless_leak_2": "high",
}

ALARM_MESSAGES = {
    "water_alarm": {
        "title": "🚨 Water Alarm Detected",
        "message": "Water alarm has been triggered on your Waterguard system. Please check your water sensors immediately.",
        "icon": "mdi:water-alert",
    },
    "water_leak": {
        "title": "💧 Water Leak Detected", 
        "message": "Water leak detected by sensor. Please check the monitored area and reset the alarm after addressing the issue.",
        "icon": "mdi:water",
    },
    "valve_alarm": {
        "title": "🔧 Valve System Alert",
        "message": "Valve system alarm detected. Please check valve status and operation.",
        "icon": "mdi:valve-closed",
    },
    "low_battery": {
        "title": "🔋 Low Battery Warning",
        "message": "Wireless sensor battery is low. Please replace the battery soon.",
        "icon": "mdi:battery-low",
    },
    "connection_lost": {
        "title": "📡 Connection Lost",
        "message": "Lost connection to Waterguard hub. Please check network connectivity.",
        "icon": "mdi:wifi-off",
    },
    "wireless_leak_1": {
        "title": "💧 Wireless Leak Sensor 1",
        "message": "Water leak detected by wireless sensor 1. Please check the monitored area and reset the alarm after addressing the issue.",
        "icon": "mdi:water",
    },
    "wireless_leak_2": {
        "title": "💧 Wireless Leak Sensor 2",
        "message": "Water leak detected by wireless sensor 2. Please check the monitored area and reset the alarm after addressing the issue.",
        "icon": "mdi:water",
    },
}


class NotificationManager:
    """Handles alarm notifications for Waterguard Linkbox."""

    def __init__(self, hass: HomeAssistant, hub: WaterguardLinkboxHub) -> None:
        """Initialize the notification manager."""
        self.hass = hass
        self.hub = hub
        self._active_alarms: dict[str, dict[str, Any]] = {}
        self._last_states: dict[str, Any] = {}
        self._notification_settings = {
            "persistent_notifications": True,
            "mobile_notifications": True,
            "email_notifications": False,
        }
        self._last_wireless_data: dict[str, Any] = {}

    async def async_setup(self) -> None:
        """Set up notification manager."""
        _LOGGER.info("Setting up Waterguard notification manager")
        
        # Clear any existing notifications on startup
        await self.clear_all_notifications()

    async def async_check_alarms(self, data: dict[str, Any], last_wireless_data: dict[str, Any]) -> None:
        """Check for alarm conditions and send notifications."""
        current_time = utcnow()
        self._last_wireless_data = last_wireless_data
        
        # Check water alarms
        await self._check_water_alarms(data.get("water", {}), current_time)
        
        # Check valve alarms
        await self._check_valve_alarms(data.get("valve", {}), current_time)
        
        # Check wireless sensor alarms
        await self._check_wireless_alarms(self._last_wireless_data, current_time)
        
        # Check connection status
        await self._check_connection_status(data, current_time)

    async def _check_water_alarms(self, water_data: dict[str, Any], current_time: datetime) -> None:
        """Check water-related alarms."""
        # - Water objects (alarm, leak1) return BACnet errors (None values) - not connected/available
        # - This means no sensor tape or water alarm system is connected to the hub
        
        # Water alarm check
        alarm_value = water_data.get("alarm")
        if alarm_value is not None:
            # We have valid data - check for alarm condition
            if alarm_value >= 1.0:
                await self._trigger_alarm("water_alarm", {
                    "value": alarm_value,
                    "timestamp": current_time,
                    "sensor": "system_alarm",
                })
            else:
                await self._clear_alarm("water_alarm")
        else:
            # No data available (BACnet error) - clear any existing alarm
            await self._clear_alarm("water_alarm")

        # Sensor tape check
        leak_value = water_data.get("leak1")
        if leak_value is not None:
            # We have valid data - check for leak condition
            if leak_value >= 1.0:
                await self._trigger_alarm("water_leak", {
                    "value": leak_value,
                    "timestamp": current_time,
                    "sensor": "sensor_tape",
                })
            else:
                await self._clear_alarm("water_leak")
        else:
            # No data available (BACnet error) - clear any existing alarm
            await self._clear_alarm("water_leak")
            
        # Also check for wireless leaks
        for i in range(1, 3):
            leak_key = f"leak{i}"
            leak_value = self._last_wireless_data.get(leak_key)
            if leak_value is not None:
                if leak_value >= 1.0:
                    await self._trigger_alarm(f"wireless_leak_{i}", {
                        "value": leak_value,
                        "timestamp": current_time,
                        "sensor": f"wireless_leak_sensor_{i}",
                    })
                else:
                    await self._clear_alarm(f"wireless_leak_{i}")
            else:
                await self._clear_alarm(f"wireless_leak_{i}")

    async def _check_valve_alarms(self, valve_data: dict[str, Any], current_time: datetime) -> None:
        """Check valve-related alarms."""
        # - num_valves: 1 (normal operation)
        # - valve_status1 and valve_status2: 4 (disconnected)
        
        num_valves = valve_data.get("num_valves")
        valve1_status = valve_data.get("valve_status1")
        valve2_status = valve_data.get("valve_status2")
        
        # Check for valve disconnection based on status values
        valve_disconnected = False
        disconnected_valves = []
        
        if valve1_status == 4:
            valve_disconnected = True
            disconnected_valves.append("valve 1")
            
        if valve2_status == 4:
            valve_disconnected = True
            disconnected_valves.append("valve 2")
        
        if valve_disconnected:
            disconnect_message = f"Valve(s) disconnected: {', '.join(disconnected_valves)}"
            await self._trigger_alarm("valve_alarm", {
                "value": disconnect_message,
                "timestamp": current_time,
                "sensor": "valve_system",
            })
        else:
            await self._clear_alarm("valve_alarm")

    async def _check_wireless_alarms(self, wireless_data: dict[str, Any], current_time: datetime) -> None:
        """Check wireless sensor alarms."""
        # Check battery level
        battery_voltage = wireless_data.get("battery_voltage")
        if battery_voltage is not None and battery_voltage < 2.5:  # Low battery threshold
            await self._trigger_alarm("low_battery", {
                "value": f"{battery_voltage:.2f}V",
                "timestamp": current_time,
                "sensor": "wireless_sensor_battery",
            })
        else:
            await self._clear_alarm("low_battery")

        # Check wireless leak sensors
        for i in range(1, 3):
            leak_key = f"leak{i}"
            leak_value = wireless_data.get(leak_key)
            alarm_type = f"wireless_leak_{i}"
            if leak_value is not None:
                if leak_value >= 1.0:
                    await self._trigger_alarm(alarm_type, {
                        "value": leak_value,
                        "timestamp": current_time,
                        "sensor": f"wireless_leak_sensor_{i}",
                    })
                else:
                    await self._clear_alarm(alarm_type)
            else:
                await self._clear_alarm(alarm_type)

    async def _check_connection_status(self, data: dict[str, Any], current_time: datetime) -> None:
        """Check connection status."""
        # If we have no data or connection issues, trigger connection alarm
        if not data or all(not section for section in data.values()):
            await self._trigger_alarm("connection_lost", {
                "value": "No data received",
                "timestamp": current_time,
                "sensor": "hub_connection",
            })
        else:
            await self._clear_alarm("connection_lost")

    async def _trigger_alarm(self, alarm_type: str, alarm_data: dict[str, Any]) -> None:
        """Trigger an alarm notification with spam prevention."""
        # Check if this is a new alarm or state change
        if alarm_type not in self._active_alarms:
            self._active_alarms[alarm_type] = alarm_data
            
            # Send notification
            await self._send_notification(alarm_type, alarm_data)
            
            _LOGGER.warning(
                "Alarm triggered: %s - %s at %s",
                alarm_type,
                alarm_data.get("value"),
                alarm_data.get("timestamp")
            )
        else:
            # Update existing alarm data but don't send duplicate notifications
            existing_alarm = self._active_alarms[alarm_type]
            
            # Only send notification if the value has changed significantly
            if (existing_alarm.get("value") != alarm_data.get("value") or 
                existing_alarm.get("sensor") != alarm_data.get("sensor")):
                
                self._active_alarms[alarm_type] = alarm_data
                _LOGGER.debug(
                    "Alarm updated: %s - %s (previous: %s)",
                    alarm_type,
                    alarm_data.get("value"),
                    existing_alarm.get("value")
                )
            else:
                # Just update timestamp without sending notification
                self._active_alarms[alarm_type]["timestamp"] = alarm_data.get("timestamp")

    async def _clear_alarm(self, alarm_type: str) -> None:
        """Clear an alarm notification."""
        if alarm_type in self._active_alarms:
            del self._active_alarms[alarm_type]
            
            # Clear persistent notification
            if self._notification_settings.get("persistent_notifications", True):
                try:
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "dismiss",
                        {"notification_id": NOTIFICATION_IDS[alarm_type]},
                        blocking=False,
                    )
                except Exception as err:
                    _LOGGER.debug("Failed to clear notification %s: %s", NOTIFICATION_IDS[alarm_type], err)
            
            _LOGGER.info("Alarm cleared: %s", alarm_type)

    async def _send_notification(self, alarm_type: str, alarm_data: dict[str, Any]) -> None:
        """Send notification through configured channels."""
        alarm_config = ALARM_MESSAGES.get(alarm_type, {})
        
        # Create persistent notification
        if self._notification_settings.get("persistent_notifications", True):
            message = f"{alarm_config.get('message', 'Alarm detected')}\n\n"
            message += f"**Sensor:** {alarm_data.get('sensor', 'Unknown')}\n"
            message += f"**Value:** {alarm_data.get('value', 'Unknown')}\n"
            message += f"**Time:** {alarm_data.get('timestamp', 'Unknown')}\n\n"
            message += "*Check your Waterguard system and reset the alarm if needed.*"
            
            await self.hass.async_add_executor_job(
                create,
                self.hass,
                message,
                alarm_config.get("title", "Waterguard Alert"),
                NOTIFICATION_IDS[alarm_type]
            )

        # Send mobile notification via notify service
        if self._notification_settings.get("mobile_notifications", True):
            await self._send_mobile_notification(alarm_type, alarm_data)

        # Fire Home Assistant event for automations
        self.hass.bus.async_fire(
            f"{DOMAIN}_alarm",
            {
                "alarm_type": alarm_type,
                "priority": ALARM_PRIORITIES.get(alarm_type, "medium"),
                "sensor": alarm_data.get("sensor"),
                "value": alarm_data.get("value"),
                "timestamp": alarm_data.get("timestamp"),
            }
        )

    async def _send_mobile_notification(self, alarm_type: str, alarm_data: dict[str, Any]) -> None:
        """Send mobile notification if notify service is available."""
        try:
            notify_service = self.hass.services.async_services().get("notify")
            if notify_service:
                alarm_config = ALARM_MESSAGES.get(alarm_type, {})
                
                # Try to send to mobile app first, then fallback to other services
                service_names = ["mobile_app", "notify", "persistent_notification"]
                
                for service_name in service_names:
                    if service_name in notify_service:
                        await self.hass.services.async_call(
                            "notify",
                            service_name,
                            {
                                "title": alarm_config.get("title", "Waterguard Alert"),
                                "message": f"{alarm_config.get('message', 'Alarm detected')} (Value: {alarm_data.get('value', 'Unknown')})",
                                "data": {
                                    "priority": ALARM_PRIORITIES.get(alarm_type, "medium"),
                                    "tag": alarm_type,
                                    "icon": alarm_config.get("icon", "mdi:alert"),
                                }
                            }
                        )
                        break
                        
        except Exception as err:
            _LOGGER.warning("Failed to send mobile notification: %s", err)

    async def clear_notification(self, alarm_type: str) -> None:
        """Clear a specific notification."""
        notification_id = f"{DOMAIN}_{alarm_type}"
        _LOGGER.debug(f"Cleared existing notification: {notification_id}")
        await self.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": notification_id},
        )

    async def clear_all_notifications(self) -> None:
        """Clear all notifications."""
        for notification_id in NOTIFICATION_IDS.values():
            try:
                await self.hass.services.async_call(
                    "persistent_notification",
                    "dismiss",
                    {"notification_id": notification_id},
                    blocking=False,
                )
                _LOGGER.debug("Cleared existing notification: %s", notification_id)
            except Exception as err:
                _LOGGER.debug("Failed to clear notification %s: %s", notification_id, err)
        
        # Reset internal state
        self._active_alarms.clear()
        self._last_states.clear()
        _LOGGER.debug("All notifications cleared and state reset")

    def is_notification_due(self, alarm_type: str) -> bool:
        """Check if a notification is due for a given alarm type."""
        if not self._notification_settings.get("persistent_notifications", True) and not self._notification_settings.get("mobile_notifications", True):
            return False

        last_time = self._active_alarms.get(alarm_type, {}).get("timestamp")
        if last_time and (datetime.now() - last_time) > timedelta(minutes=1):
            return True
            
        return False

    async def async_send_notification(
        self,
        alarm_type: str,
        sensor_id: str,
        value: Any,
        timestamp: datetime,
        is_test: bool = False,
    ) -> None:
        """Send a notification."""
        if not self._notification_settings.get("persistent_notifications", True) and not self._notification_settings.get("mobile_notifications", True):
            return

        title, message = self._format_notification(alarm_type, sensor_id, value, timestamp, is_test)
        notification_id = f"{DOMAIN}_{alarm_type}"

        if self._notification_settings.get("persistent_notifications", True):
            await self.hass.async_add_executor_job(
                create,
                self.hass,
                message,
                title,
                notification_id
            )
        
        if self._notification_settings.get("mobile_notifications", True):
            await self.hass.services.async_call(
                "notify",
                "mobile_app",
                {
                    "title": title,
                    "message": message,
                    "data": {
                        "priority": ALARM_PRIORITIES.get(alarm_type, "medium"),
                        "tag": alarm_type,
                        "icon": ALARM_MESSAGES.get(alarm_type, {}).get("icon", "mdi:alert"),
                    }
                }
            )

        self._active_alarms[alarm_type] = {"value": value, "timestamp": timestamp, "sensor": sensor_id}

    def _format_notification(
        self, alarm_type: str, sensor_id: str, value: Any, timestamp: datetime, is_test: bool
    ) -> tuple[str, str]:
        """Format notification title and message."""
        test_prefix = "[TEST] " if is_test else ""
        
        if alarm_type == "water_alarm":
            title = f"{test_prefix}Water Alarm Detected"
            message = f"Water alarm has been triggered on your Waterguard system. Please check your water sensors immediately.\n\n" \
                      f"**Sensor:** `{sensor_id}`\n" \
                      f"**Value:** `{value}`\n" \
                      f"**Time:** `{timestamp}`\n\n" \
                      "Check your Waterguard system and reset the alarm if needed."
        elif alarm_type == "water_leak":
            title = f"{test_prefix}Water Leak Detected"
            message = f"Water leak detected by sensor. Please check the monitored area and reset the alarm after addressing the issue.\n\n" \
                      f"**Sensor:** `{sensor_id}`\n" \
                      f"**Value:** `{value}`\n" \
                      f"**Time:** `{timestamp}`\n\n" \
                      "Check your Waterguard system and reset the alarm if needed."
        elif alarm_type == "valve_alarm":
            title = f"{test_prefix}Valve Alarm"
            message = f"A valve alarm has been triggered on your Waterguard system. This may indicate a disconnected or malfunctioning valve.\n\n" \
                      f"**Sensor:** `{sensor_id}`\n" \
                      f"**Value:** `{value}` (1 = Unknown)\n" \
                      f"**Time:** `{timestamp}`\n\n" \
                      "Please check the valve connections and system status."
        elif alarm_type == "low_battery":
            title = f"{test_prefix}Low Battery Warning"
            message = f"Low battery detected on a wireless sensor. Please replace the battery soon to ensure continued operation.\n\n" \
                      f"**Sensor:** `{sensor_id}`\n" \
                      f"**Battery Level:** `{value}%`\n" \
                      f"**Time:** `{timestamp}`"
        elif alarm_type == "connection_lost":
            title = f"{test_prefix}Connection Lost"
            message = f"Connection to the Waterguard hub has been lost. Please check the hub's power and network connection.\n\n" \
                      f"**Last Seen:** `{timestamp}`"
        else:
            title = f"{test_prefix}Waterguard Alert"
            message = f"An unknown alarm has been triggered.\n\n" \
                      f"**Sensor:** `{sensor_id}`\n" \
                      f"**Value:** `{value}`\n" \
                      f"**Time:** `{timestamp}`"
                      
        return title, message 

    def update_notification_settings(self, settings: dict[str, Any]) -> None:
        """Update notification settings."""
        _LOGGER.debug("Updating notification settings: %s", settings)
        self._notification_settings.update(settings)
        _LOGGER.info("Notification settings updated: persistent=%s, mobile=%s", 
                    self._notification_settings.get("persistent_notifications", True),
                    self._notification_settings.get("mobile_notifications", True)) 