"""Constants for the Waterguard Linkbox integration."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)

DOMAIN = "waterguard_linkbox"

# Configuration keys
CONF_DEVICE_ID = "device_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_FAST_POLL_INTERVAL = "fast_poll_interval"
CONF_ENABLE_NOTIFICATIONS = "enable_notifications"
CONF_NOTIFICATION_MOBILE = "notification_mobile"
CONF_NOTIFICATION_PERSISTENT = "notification_persistent"
CONF_POLL_WIRELESS = "poll_wireless"
CONF_WIRELESS_POLL_INTERVAL = "wireless_poll_interval"
CONF_DEBOUNCE_SECONDS = "debounce_seconds"

VERSION = "1"

# Firmware version for the Linkbox
FIRMWARE_VERSION = "2.314.1"

# Default values
DEFAULT_PORT = 47808
DEFAULT_SCAN_INTERVAL = 2  # Standard polling interval in seconds (local network)
DEFAULT_FAST_POLL_INTERVAL = 1  # Fast polling when alarm is active (seconds)
DEFAULT_ENABLE_NOTIFICATIONS = True
DEFAULT_NOTIFICATION_MOBILE = True
DEFAULT_NOTIFICATION_PERSISTENT = True
DEFAULT_POLL_WIRELESS = True  # Poll hub wireless data periodically; hub still pushes alarms
DEFAULT_WIRELESS_POLL_INTERVAL = 30  # Seconds between wireless refresh in normal operation
DEFAULT_DEBOUNCE_SECONDS = 1

# Device information - Updated with correct branding
MANUFACTURER = "Fell Tech"
MODEL = "Abra Linkbox+ / Waterguard Hub"

# BACnet object types
BACNET_OBJECT_TYPES = {
    0: "Analog Input",
    1: "Analog Output", 
    2: "Analog Value",
    3: "Binary Input",
    4: "Binary Output",
    5: "Binary Value",
    8: "Device",
    13: "Multi-state Input",
    14: "Multi-state Output",
    19: "Multi-state Value",
    20: "Notification Class",
    56: "Network Port",
}

# Water monitoring object definitions
WATER_OBJECTS = {
    "alarm": {"type": 0, "instance": 7, "name": "waterMonitor:8.alarm"},
    "leak1": {"type": 0, "instance": 9, "name": "waterMonitor:8.leak"},
    "reset_leak": {"type": 14, "instance": 10, "name": "waterMonitor:8.resetLeak"},
}

# Valve monitoring object definitions
VALVE_OBJECTS = {
    "num_valves": {"type": 13, "instance": 3, "name": "valveMonitor:8.numValves"},
    "valve_status1": {"type": 13, "instance": 5, "name": "valveMonitor:8.valveStatus1"},
    "valve_status2": {"type": 13, "instance": 6, "name": "valveMonitor:8.valveStatus2"},
    "control": {"type": 14, "instance": 1, "name": "valveControl:8.openClose"},
}

# Device information objects
DEVICE_OBJECTS = {
    "device": {"type": 8, "instance": None, "name": "Abra Linkbox+"},  # Instance = device_id
    "network_port": {"type": 56, "instance": 1, "name": "BACnet/IP Port"},
}

# Wireless sensor objects (if present)
WIRELESS_SENSOR_OBJECTS = {
    "leak1": {"type": 0, "instance": 11, "name": "ID.waterMonitor.X.leak1"},
    "leak2": {"type": 0, "instance": 12, "name": "ID.waterMonitor.X.leak2"},
    "temperature": {"type": 0, "instance": 13, "name": "ID.waterMonitor.X.temperature"},
    "humidity": {"type": 0, "instance": 14, "name": "ID.waterMonitor.X.humidity"},
    "battery_voltage": {"type": 0, "instance": 15, "name": "ID.waterMonitor.X.batteryVoltage"},
}

# Reset values for different alarm types
RESET_VALUES = {
    "water_leak": 2,
    "water_alarm": 2,
}

# State value interpretations
STATE_MAPPINGS = {
    "water_alarm": {
        0: "normal",
        1: "alarm",
    },
    "water_leak": {
        0: "dry",
        1: "wet",
    },
    "valve_status": {
        1: "unknown",
        2: "closed",
        3: "open",
        4: "disconnected",
        1087: "disconnected",
    },
    "valve_control": {
        1: "n/a",
        2: "close",
        3: "open",
    },
    "reset_leak": {
        1: "n/a",
        2: "reset",
    },
}

# Battery level
BATTERY_LEVELS = {
    3.3: 100,  # 100% = 3.3V
    2.2: 15,   # 15% = 2.2V
}

ALARM_STATES = {
    0: "normal",
    1: "alarm",
  }

BINARY_SENSOR_DESCRIPTIONS = [
    BinarySensorEntityDescription(
        key="water_alarm",
        name="Waterguard System Alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    BinarySensorEntityDescription(
        key="water_leak1",
        name="Waterguard Sensor Tape",
        device_class=BinarySensorDeviceClass.MOISTURE,
    ),
    BinarySensorEntityDescription(
        key="valve_disconnected",
        name="Waterguard Valve Disconnected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    BinarySensorEntityDescription(
        key="wireless_leak1",
        name="Waterguard Wireless Sensor Point 1",
        device_class=BinarySensorDeviceClass.MOISTURE,
    ),
    BinarySensorEntityDescription(
        key="wireless_leak2", 
        name="Waterguard Wireless Sensor Point 2",
        device_class=BinarySensorDeviceClass.MOISTURE,
    ),
    BinarySensorEntityDescription(
        key="hub_connected",
        name="Waterguard Hub Connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
]

# Entity names and descriptions
ENTITY_DESCRIPTIONS = {
    "water_alarm": {
        "name": "Waterguard System Alarm",
        "icon": "mdi:water-alert",
        "device_class": "problem",
        "description": "System water alarm (Pågående vannalarm - from BACnet object waterMonitor.X.alarm)",
    },
    "water_leak1": {
        "name": "Waterguard Sensor Tape",
        "icon": "mdi:water",
        "device_class": "moisture",
        "description": "Wired sensor tape status (Sensortape status - from BACnet object waterMonitor.X.leak1)",
    },
    "num_valves": {
        "name": "Waterguard Number of Valves",
        "icon": "mdi:counter",
        "description": "Number of valves connected to the system",
    },
    "valve_status1": {
        "name": "Waterguard Valve 1 Status",
        "icon": "mdi:valve",
        "description": "Status of valve 1 (unknown/closed/open)",
    },
    "valve_status2": {
        "name": "Waterguard Valve 2 Status", 
        "icon": "mdi:valve",
        "description": "Status of valve 2 (unknown/closed/open)",
    },
    "valve_control": {
        "name": "Waterguard Main Valve Control",
        "icon": "mdi:valve",
        "description": "Control main water valve (open/close)",
    },
    "reset_leak": {
        "name": "Waterguard Reset Water Alarm",
        "icon": "mdi:restart-alert",
        "description": "Reset water leak alarm",
    },
    # Wireless sensor entities (if present)
    "wireless_leak1": {
        "name": "Waterguard Wireless Sensor Point 1",
        "icon": "mdi:water",
        "device_class": "moisture",
        "description": "Wireless sensor point 1 (from BACnet object ID.waterMonitor.X.leak1)",
    },
    "wireless_leak2": {
        "name": "Waterguard Wireless Sensor Point 2",
        "icon": "mdi:water",
        "device_class": "moisture",
        "description": "Wireless sensor point 2 (from BACnet object ID.waterMonitor.X.leak2)",
    },
    "wireless_temperature": {
        "name": "Waterguard Wireless Temperature",
        "icon": "mdi:thermometer",
        "device_class": "temperature",
        "unit": "°C",
        "description": "Temperature from wireless sensor (from BACnet object ID.waterMonitor.X.temperature)",
    },
    "wireless_humidity": {
        "name": "Waterguard Wireless Humidity",
        "icon": "mdi:water-percent",
        "device_class": "humidity",
        "unit": "%",
        "description": "Humidity from wireless sensor (from BACnet object ID.waterMonitor.X.humidity)",
    },
    "wireless_battery": {
        "name": "Waterguard Wireless Sensor Battery",
        "icon": "mdi:battery",
        "device_class": "battery",
        "unit": "%",
        "description": "Battery level of wireless sensor (from BACnet object ID.waterMonitor.X.batteryVoltage)",
    },
} 