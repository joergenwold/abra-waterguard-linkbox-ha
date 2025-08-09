"""Waterguard Linkbox communication class."""
from __future__ import annotations

import logging
import socket
import struct
from typing import Any, Optional, Union
import math

from .const import VALVE_OBJECTS, WATER_OBJECTS, DOMAIN, FIRMWARE_VERSION

_LOGGER = logging.getLogger(__name__)


class WaterguardLinkboxHub:
    """Waterguard Linkbox communication class."""

    def __init__(self, host: str, port: int, device_id: int) -> None:
        """Initialize the hub."""
        self.host = host
        self.port = port
        self.device_id = device_id
        self.local_port = self._find_available_port()
        self._socket = None

    def _find_available_port(self) -> int:
        """Find an available local port starting from 47809."""
        for port in range(47809, 47900):
            try:
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                test_sock.bind(("0.0.0.0", port))
                test_sock.close()
                return port
            except OSError:
                continue
        # Fallback to system-assigned port
        return 0
    
    def _create_socket(self) -> socket.socket:
        """Create and configure a UDP socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(5.0)
        sock.bind(("0.0.0.0", self.local_port))
        return sock

    def _encode_bacnet_unsigned(self, value: int) -> bytes:
        """Encode an unsigned integer for BACnet.
        
        Values 254 and 255 are special markers in BACnet to indicate
        that the following bytes represent a 2-byte or 4-byte value.
        """
        if value < 254:
            return bytes([value])
        elif value < 65536:
            return bytes([254, (value >> 8) & 0xFF, value & 0xFF])
        else:
            return bytes([255, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF])

    def _create_read_property_request(
        self, object_type: int, object_instance: int, property_id: int = 85
    ) -> bytes:
        """Create ReadProperty request."""
        # BVLC Header
        bvlc_header = struct.pack(">BBH", 0x81, 0x0A, 0)

        # NPDU Header
        npdu_header = struct.pack(">BB", 0x01, 0x04)

        # APDU Header
        invoke_id = 1
        apdu_header = struct.pack(">BBB", 0x00, 0x00, invoke_id)

        # Service Choice
        service_choice = bytes([0x0C])  # ReadProperty

        # Object Identifier
        obj_id = (object_type << 22) | object_instance
        obj_identifier = b"\x0C" + struct.pack(">L", obj_id)

        # Property Identifier
        prop_id = b"\x19" + self._encode_bacnet_unsigned(property_id)

        # Combine all parts
        packet = (
            bvlc_header + npdu_header + apdu_header + service_choice + obj_identifier + prop_id
        )

        # Update BVLC length
        packet = packet[:2] + struct.pack(">H", len(packet)) + packet[4:]

        return packet

    def _create_write_property_request(
        self, object_type: int, object_instance: int, value: float, property_id: int = 85
    ) -> bytes:
        """Create WriteProperty request."""
        # BVLC Header
        bvlc_header = struct.pack(">BBH", 0x81, 0x0A, 0)

        # NPDU Header
        npdu_header = struct.pack(">BB", 0x01, 0x04)

        # APDU Header
        invoke_id = 1
        apdu_header = struct.pack(">BBB", 0x00, 0x00, invoke_id)

        # Service Choice
        service_choice = bytes([0x0F])  # WriteProperty

        # Object Identifier
        obj_id = (object_type << 22) | object_instance
        obj_identifier = b"\x0C" + struct.pack(">L", obj_id)

        # Property Identifier
        prop_id = b"\x19" + self._encode_bacnet_unsigned(property_id)

        # Property Value
        if isinstance(value, float) and value.is_integer():
            value = int(value)

        if isinstance(value, int):
            if value <= 0xFF:
                value_data = b"\x3E\x21" + bytes([value]) + b"\x3F"
            elif value <= 0xFFFF:
                value_data = b"\x3E\x22" + struct.pack(">H", value) + b"\x3F"
            else:
                value_data = b"\x3E\x24" + struct.pack(">I", value) + b"\x3F"
        else:
            # Real value
            value_data = b"\x3E\x44" + struct.pack(">f", value) + b"\x3F"

        packet = (
            bvlc_header
            + npdu_header
            + apdu_header
            + service_choice
            + obj_identifier
            + prop_id
            + value_data
        )

        # Update BVLC length
        packet = packet[:2] + struct.pack(">H", len(packet)) + packet[4:]

        return packet

    def _send_request(self, packet: bytes, retries: int = 2) -> Optional[bytes]:
        """Send a request and get a response, with retries."""
        for attempt in range(retries + 1):
            sock = None
            try:
                sock = self._create_socket()
                sock.sendto(packet, (self.host, self.port))
                data, addr = sock.recvfrom(1024)
                return data
            except socket.timeout:
                _LOGGER.debug("Timeout communicating with Waterguard hub (attempt %d/%d)", attempt + 1, retries + 1)
            except Exception as err:
                _LOGGER.debug("Error communicating with Waterguard hub (attempt %d/%d): %s", attempt + 1, retries + 1, err)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass  # Ignore errors during cleanup
        
        _LOGGER.warning("Request failed after %d retries", retries)
        return None

    def _parse_value(self, data: bytes, object_type: int = None, expected_range: tuple = None, sensor_key: str = None) -> Optional[float]:
        """Parse a numeric value from a BACnet response."""
        try:
            # Validate minimum data length
            if not data or len(data) < 4:
                _LOGGER.debug(f"Data too short: {len(data) if data else 0} bytes")
                return None
            
            # Validate data length doesn't exceed reasonable limits
            if len(data) > 1024: 
                _LOGGER.warning(f"Response too large: {len(data)} bytes, truncating")
                data = data[:1024]
                
            # A set error bit in a BACnet response usually indicates an error.
            # However, water sensors use a format where this bit is set
            # but does not signify an error.
            if (data[3] & 0x02) != 0:
                # Check for the specific water sensor format (0x17 at byte 3)
                if (len(data) >= 4 and data[3] == 0x17):
                    _LOGGER.debug("Water sensor format detected; ignoring error bit.")
                else:
                    _LOGGER.debug("BACnet error response detected.")
                    return None
            
            _LOGGER.debug(f"Parsing response: {data.hex()}")

            # Route to the appropriate parser based on object type and response pattern.
            if self._is_wireless_sensor_response(data, object_type):
                return self._parse_wireless_sensor_response(data, sensor_key or "unknown")
            else:
                return self._parse_wired_sensor_value(data, object_type, expected_range)

        except (IndexError, struct.error) as err:
            _LOGGER.warning(f"BACnet parsing error (malformed data): {err}")
            return None
        except Exception as err:
            _LOGGER.debug(f"Unexpected error parsing BACnet value: {err}")
            return None

    def _is_wireless_sensor_response(self, data: bytes, object_type: int) -> bool:
        """Determine if this is a wireless sensor response based on format patterns."""

        # Only identify as wireless if we have the exact wireless sensor format
        # AND it's an analog input (type 0) which is what wireless sensors use
        if (object_type == 0 and 
            len(data) >= 22 and 
            data[:4] == b'\x81\x0a\x00\x17' and 
            data[16:18] == b'\x3e\x44'):
            return True
                
        return False

    def _parse_wired_sensor_value(self, data: bytes, object_type: int, expected_range: tuple = None) -> Optional[float]:
        """Parse values from wired sensors (non-wireless BACnet objects)."""
        try:
            # Look for enumerated value first (0x91)
            if b"\x91" in data:
                enum_start = data.find(b"\x91")
                if enum_start >= 0 and enum_start + 1 < len(data):
                    value = float(data[enum_start + 1])
                    _LOGGER.debug(f"Found enumerated value: {value}")
                    return self._apply_multi_state_mapping(value, object_type)
                        
            # Look for unsigned integer (0x21)
            if b"\x21" in data:
                uint_start = data.find(b"\x21")
                if uint_start >= 0 and uint_start + 1 < len(data):
                    # - Single byte values: 1, 4 (normal values)
                    # - Two byte values: 319, 1087 (special/disconnected values)
                    
                    # Always prefer single-byte for normal operation
                    single_byte_value = data[uint_start + 1]
                    
                    # Check if we have enough bytes for two-byte value
                    if uint_start + 3 <= len(data):
                        two_byte_value = (data[uint_start + 1] << 8) | data[uint_start + 2]
                        
                        # 319 and 1087 are special disconnected values
                        # Use two-byte values for these special cases
                        if two_byte_value in [319, 1087]:
                            _LOGGER.debug(f"Using two-byte special value: {two_byte_value}")
                            return self._apply_multi_state_mapping(float(two_byte_value), object_type)
                    
                    # For normal operation, use single-byte value
                    _LOGGER.debug(f"Using single-byte value: {single_byte_value}")
                    return self._apply_multi_state_mapping(float(single_byte_value), object_type)
            
            # Boolean value (0x10)
            if b"\x10" in data:
                bool_start = data.find(b"\x10")
                if bool_start >= 0 and bool_start + 1 < len(data):
                    value = float(data[bool_start + 1])
                    _LOGGER.debug(f"Found boolean value: {value}")
                    return value
            
            # Standard real value (0x44) parsing - for wired analog objects
            if b"\x44" in data:
                real_start = data.find(b"\x44")
                if real_start >= 0 and real_start + 5 <= len(data):
                    real_bytes = data[real_start + 1 : real_start + 5]
                    try:
                        value = struct.unpack(">f", real_bytes)[0]
                        
                        # Validate the unpacked float value
                        if math.isnan(value) or math.isinf(value):
                            _LOGGER.debug(f"Invalid float value (NaN/Inf): {value}")
                            return None
                        
                        _LOGGER.debug(f"Found real value: {value}")
                        
                        # Apply expected range validation if provided
                        if expected_range:
                            min_val, max_val = expected_range
                            if min_val <= value <= max_val:
                                return value
                            else:
                                _LOGGER.debug(f"Real value {value} outside expected range {expected_range}")
                                return None
                        else:
                            # General validation for analog values
                            if -100 <= value <= 10000:
                                return value
                            else:
                                _LOGGER.debug(f"Real value out of general range: {value}")
                                return None
                    except (struct.error, ValueError) as e:
                        _LOGGER.debug(f"Error unpacking real value from {real_bytes.hex()}: {e}")
                        return None

            _LOGGER.debug("No valid value found in wired sensor response")
            return None
            
        except Exception as err:
            _LOGGER.debug(f"Error parsing wired sensor value: {err}")
            return None

    def _parse_wireless_sensor_response(self, data: bytes, sensor_key: str) -> Optional[float]:
        """Parse wireless sensor response with the specific format from test results."""
        try:
            _LOGGER.debug(f"Parsing wireless sensor response for {sensor_key}: {data.hex()}")
            
            # Handling for water sensor format (different from standard wireless format)
            if (len(data) == 23 and 
                data[:4] == b'\x81\x0a\x00\x17' and 
                data[4:8] == b'\x01\x00\x30\x01' and
                data[16:19] == b'\x3e\x44\x3f'):
                if len(data) >= 20:
                    value_byte = data[19]
                    _LOGGER.debug(f"Water sensor response - extracting value from position 19: {value_byte} (0x{value_byte:02x})")
                    if value_byte == 0x00:
                        return 0.0  # Normal/dry
                    elif value_byte == 0x80:
                        return 1.0  # Alarm/wet
                    else:
                        _LOGGER.debug(f"Unknown water sensor value byte: 0x{value_byte:02x}")
                        return 0.0  # Treat as dry
                
                # Fallback - if we can't extract the value, treat as normal/dry
                _LOGGER.debug("Water sensor format detected but could not extract value - assuming normal/dry")
                return 0.0
            
            # Standard wireless sensor format with 3E44 pattern
            if len(data) < 22:
                _LOGGER.debug(f"Response too short for wireless format: {len(data)} bytes")
                return None
                
            # Check for the 3E44 pattern at position 16-18
            if data[16:18] != b"\x3e\x44":
                _LOGGER.debug(f"Expected 3E44 pattern not found at position 16")
                return None
            
            # The float value is at positions 18-22 (4 bytes after 3E44)
            if len(data) >= 22:
                float_bytes = data[18:22]
                _LOGGER.debug(f"Extracting float value from bytes 18-22: {float_bytes.hex()}")
                
                try:
                    value = struct.unpack(">f", float_bytes)[0]
                    
                    # Validate the unpacked float value
                    if math.isnan(value) or math.isinf(value):
                        _LOGGER.debug(f"Invalid wireless float value (NaN/Inf): {value}")
                        return None
                        
                    _LOGGER.debug(f"Successfully decoded wireless sensor value for {sensor_key}: {value}")
                    
                    # 0.0 (dry) or 1.0 (wet) are valid values
                    if sensor_key in ["leak1", "leak2"]:
                        if value == 1.0:
                            return 1.0  # Wet
                        elif value == 0.0:
                            return 0.0  # Dry
                        else:
                            _LOGGER.debug(f"Leak sensor value {value} is not exactly 0.0 or 1.0, treating as dry")
                            return 0.0  # Default to dry for any other value
                    elif sensor_key == "temperature":
                        if -50 <= value <= 100:
                            return value
                        else:
                            _LOGGER.debug(f"Temperature value {value} outside reasonable range")
                            return None
                    elif sensor_key == "humidity":
                        if 0 <= value <= 150:
                            return value
                        else:
                            _LOGGER.debug(f"Humidity value {value} outside expected range 0-100")
                            return None
                    elif sensor_key == "battery_voltage":
                        if 1.5 <= value <= 4.0:
                            return value
                        else:
                            _LOGGER.debug(f"Battery voltage {value} outside expected range 1.5-4.0V")
                            return None
                    else:
                        if not (math.isnan(value) or math.isinf(value)):
                            return value
                        else:
                            _LOGGER.debug(f"Invalid float value for {sensor_key}: {value}")
                            return None
                            
                except (struct.error, ValueError) as e:
                    _LOGGER.debug(f"Error decoding float value from {float_bytes.hex()}: {e}")
                    return None
            
            _LOGGER.debug("Could not extract valid float value from wireless sensor response")
            return None
                
        except Exception as e:
            _LOGGER.debug(f"Error in wireless sensor parsing: {e}")
            return None

    def _apply_multi_state_mapping(self, value: float, object_type: int) -> float:
        """Apply multi-state object value mapping for special cases."""
        if object_type == 13: # Multi-state Input
            # Pass through special values so the coordinator/sensor can handle them
            if value in [319.0, 1087.0]:
                return value
            # For "valve_status", it represents states.
            if 0 <= value <= 10:
                return value
            else:
                _LOGGER.debug(f"Multi-state value out of normal range: {value}")
                return value # Return original value for upstream handling

        elif object_type == 14:  # Multi-state Output (valve control, reset)
            # Handle special cases for disconnected/unavailable outputs
            if value == 319.0:
                return 1  # Return 1 (N/A) for disconnected valve control
            elif 0 <= value <= 10:
                return value
            else:
                _LOGGER.debug(f"Multi-state output value out of range: {value}, treating as N/A")
                return 1  # Default to "N/A" for unknown large values
        else:
            # For other object types, return value as-is
            return value

    def test_connection(self) -> bool:
        """Test connection to the hub."""
        try:
            # Try to read device name
            packet = self._create_read_property_request(8, self.device_id, 77)
            response = self._send_request(packet)
            return response is not None
        except Exception as err:
            _LOGGER.debug("Connection test failed: %s", err)
            return False

    @property
    def is_connected(self) -> bool:
        """Return if hub is connected."""
        return self.test_connection()

    def get_device_info(self) -> dict[str, Any]:
        """Get device information."""
        return {
            "identifiers": {(DOMAIN, f"{self.host}_{self.device_id}")},
            "name": f"Waterguard Hub ({self.host})",
            "manufacturer": "Fell Tech",
            "model": "Abra Linkbox+ / Waterguard Hub",
            "sw_version": FIRMWARE_VERSION,
        }

    def read_water_status(self) -> dict[str, Any]:
        """Read water monitoring system status."""
        status = {}
        
        for key, obj in WATER_OBJECTS.items():
            if obj["type"] == 20:
                continue
                
            packet = self._create_read_property_request(obj["type"], obj["instance"])
            response = self._send_request(packet)
            
            if response:
                _LOGGER.debug(f"Water {key} response: {response.hex()}")
                expected_range = None
                if key == "alarm":
                    expected_range = (0, 1)  # 0=normal, 1=alarm
                elif key == "leak1":
                    expected_range = (0, 1)  # 0=dry, 1=wet
                    
                value = self._parse_value(response, obj["type"], expected_range, key)
                status[key] = value
                _LOGGER.debug(f"Water {key} parsed value: {value}")
            else:
                _LOGGER.debug(f"Water {key}: No response received")
                status[key] = None
                
        return status

    def read_valve_status(self) -> dict[str, Any]:
        """Read valve system status, adapting to the number of connected valves."""
        status = {}

        # 1. Read the number of connected valves (map raw codes to real counts).
        num_valves_obj = VALVE_OBJECTS["num_valves"]
        packet = self._create_read_property_request(num_valves_obj["type"], num_valves_obj["instance"])
        response = self._send_request(packet)
        
        num_valves = 0  # Interpreted valve count
        raw_num_valves = None

        if response:
            parsed_value = self._parse_value(response, num_valves_obj["type"], None, "num_valves")
            if parsed_value is not None:
                raw_num_valves = int(parsed_value)
                # Map raw codes to interpreted counts:
                # 2 -> 1 valve, 3 -> 2 valves, 319 -> 0 (system disconnected/no functional valves)
                if raw_num_valves == 319:
                    num_valves = 0
                elif raw_num_valves == 2:
                    num_valves = 1
                elif raw_num_valves == 3:
                    num_valves = 2
                else:
                    num_valves = raw_num_valves
                status["num_valves"] = num_valves
            else:
                status["num_valves"] = None
        else:
            status["num_valves"] = None

        # 2. Read valve control status (always present).
        control_obj = VALVE_OBJECTS["control"]
        packet = self._create_read_property_request(control_obj["type"], control_obj["instance"])
        response = self._send_request(packet)
        if response:
            status["control"] = self._parse_value(response, control_obj["type"], None, "control")
        else:
            status["control"] = None

        # 3. Read status only for the number of valves reported to be connected.
        if num_valves is not None and num_valves >= 1:
            obj = VALVE_OBJECTS["valve_status1"]
            packet = self._create_read_property_request(obj["type"], obj["instance"])
            response = self._send_request(packet)
            status["valve_status1"] = self._parse_value(response, obj["type"], None, "valve_status1") if response else None
        else:
            status["valve_status1"] = None # Ensure key is None if no valve

        if num_valves is not None and num_valves >= 2:
            obj = VALVE_OBJECTS["valve_status2"]
            packet = self._create_read_property_request(obj["type"], obj["instance"])
            response = self._send_request(packet)
            status["valve_status2"] = self._parse_value(response, obj["type"], None, "valve_status2") if response else None
        else:
            status["valve_status2"] = None # Ensure key is None if not present
                
        return status

    def control_valve(self, action: str) -> bool:
        """Control valve."""
        # Use state mappings from constants for consistency
        from .const import STATE_MAPPINGS
        
        # Map user actions to BACnet values based on official documentation
        action_values = {
            "open": 3,      # Open state
            "close": 2,     # Close state
            "n/a": 1,      # N/A state
        }
        
        if action.lower() not in action_values:
            _LOGGER.error("Invalid valve action: %s. Valid actions: %s", action, list(action_values.keys()))
            return False
            
        value = action_values[action.lower()]
        obj = VALVE_OBJECTS["control"]
        
        packet = self._create_write_property_request(obj["type"], obj["instance"], value)
        response = self._send_request(packet)
        
        # A response to a write request indicates success.
        return response is not None

    def reset_water_alarm(self, reset_value: int = 2) -> bool:
        """Reset water alarm using the confirmed working reset value."""
        obj = WATER_OBJECTS["reset_leak"]
        
        _LOGGER.info(f"Attempting to reset water alarm using object type {obj['type']}, instance {obj['instance']}, value {reset_value}")

        packet = self._create_write_property_request(obj["type"], obj["instance"], reset_value)
        _LOGGER.debug(f"Reset packet: {packet.hex()}")
        
        response = self._send_request(packet, retries=3)
        
        if response:
            _LOGGER.info("Water alarm reset command sent successfully with value %d, response: %s", reset_value, response.hex())
            return True
        else:
            _LOGGER.error("Failed to send water alarm reset command - no response received")
            return False

    def reset_leak_alarm(self) -> bool:
        """Legacy method - redirect to new reset method."""
        return self.reset_water_alarm(2)

    def read_device_info(self) -> dict[str, Any]:
        """Read device information."""
        device_info = {}
        
        # Read device name
        packet = self._create_read_property_request(8, self.device_id, 77)
        response = self._send_request(packet)
        if response:
            device_info["name"] = "Device Name Available"
        
        # Read firmware revision
        packet = self._create_read_property_request(8, self.device_id, 139)
        response = self._send_request(packet)
        if response:
            device_info["firmware_revision"] = "Firmware Available"
        
        # Read application software version
        packet = self._create_read_property_request(8, self.device_id, 12)
        response = self._send_request(packet)
        if response:
            device_info["app_version"] = "Application Version Available"
        
        return device_info

    def discover_wireless_sensors(self) -> dict[str, Any]:
        """Discover available wireless sensors and get their current values."""
        discovered_sensors = {}
        
        _LOGGER.warning("🔍 Hub: Starting wireless sensor discovery...")
        
        # Scan common wireless sensor instance ranges
        # Priority 1: Analog inputs (type 0) - provides real sensor values (temperature, humidity, etc.)
        # Priority 2: Multi-state inputs (type 13) - provides status indicators (online/offline)
        scan_ranges = [
            # Try analog inputs first - these give real values when sensors are actively transmitting
            (0, range(11, 16)),  # Instances 11-15 (analog inputs) - real sensor data
            # Fallback to multi-state inputs - these show status/connectivity when sensors are in standby
            (13, range(11, 16)), # Multi-state inputs 11-15 (status indicators)
        ]
        
        sensor_type_mapping = {
            11: ("leak1", (0.0, 1.5)),      # 0.0=dry, 1.0=wet
            12: ("leak2", (0.0, 1.5)),      # 0.0=dry, 1.0=wet
            13: ("temperature", (-50, 100)),
            14: ("humidity", (0, 150)),
            15: ("battery_voltage", (0, 5)),
        }
        
        total_found = 0
        
        for object_type, instance_range in scan_ranges:
            _LOGGER.warning(f"📡 Scanning object type {object_type}, instances {min(instance_range)}-{max(instance_range)}")
            
            for instance in instance_range:
                # Skip if we already found this sensor at a higher priority object type
                sensor_key, expected_range = sensor_type_mapping.get(instance, (f"unknown_{instance}", None))
                if sensor_key in discovered_sensors:
                    _LOGGER.warning(f"⏭️ Skipping {sensor_key} at {object_type}:{instance} - already found")
                    continue
                    
                try:
                    _LOGGER.warning(f"🔄 Testing {sensor_key} at {object_type}:{instance}")
                    packet = self._create_read_property_request(object_type, instance)
                    response = self._send_request(packet, retries=2)
                    
                    if response:
                        _LOGGER.warning(f"📨 Got response for {sensor_key}: {response.hex()}")
                        value = None
                        if object_type == 0:
                            value = self._parse_wireless_sensor_response(response, sensor_key)
                            if value is not None:
                                _LOGGER.debug(f"Specialized parser succeeded for {sensor_key}: {value}")
                        
                        # If parsing failed, fall back to general parsing
                        if value is None:
                            if object_type == 0:
                                expanded_ranges = {
                                    "leak1": (0, 2),
                                    "leak2": (0, 2),
                                    "temperature": (-50, 100),
                                    "humidity": (0, 150),
                                    "battery_voltage": (0, 5)
                                }
                                expected_range = expanded_ranges.get(sensor_key, expected_range)

                            pattern_3e44 = b"\x3e\x44"
                            if object_type == 0 and pattern_3e44 in response:
                                pattern_start = response.find(pattern_3e44)
                                _LOGGER.debug(f"Found 3E44 pattern at position {pattern_start} for {sensor_key}")
                                
                                if b"\x3f" in response[pattern_start:]:
                                    end_3f = response.find(b"\x3f", pattern_start + 1)
                                    _LOGGER.debug(f"Found 3F marker at position {end_3f} for {sensor_key}")
                                    
                                    between_bytes = response[pattern_start + 2 : end_3f]
                                    _LOGGER.debug(f"Bytes between 3E44 and 3F for {sensor_key}: {between_bytes.hex()} (length: {len(between_bytes)})")
                            
                            value = self._parse_value(response, object_type, expected_range, sensor_key)
                        
                        # Process  value
                        if value is not None:
                            # For analog inputs, prioritize realistic sensor values
                            if object_type == 0:
                                # Accept any reasonable analog value
                                if expected_range:
                                    min_val, max_val = expected_range
                                    if min_val <= value <= max_val:
                                        # Always prefer analog data over status data
                                        if sensor_key in discovered_sensors:
                                            existing_type = discovered_sensors[sensor_key].get("data_type")
                                            if existing_type == "status":
                                                _LOGGER.warning(f"🔄 Replacing status data with real analog data for {sensor_key}")
                                        
                                        discovered_sensors[sensor_key] = {
                                            "value": value,
                                            "object_type": object_type,
                                            "instance": instance,
                                            "discovery_time": "startup",
                                            "data_type": "analog"
                                        }
                                        total_found += 1
                                        _LOGGER.warning(f"✅ Found wireless sensor {sensor_key} at {object_type}:{instance} = {value} (REAL DATA)")  # Changed to WARNING
                                    else:
                                        _LOGGER.warning(f"❌ Analog value {value} at {object_type}:{instance} outside range {expected_range}")  # Changed to WARNING
                                else:
                                    # Unknown analog sensor
                                    if 0.1 <= value <= 200:
                                        discovered_sensors[sensor_key] = {
                                            "value": value,
                                            "object_type": object_type,
                                            "instance": instance,
                                            "discovery_time": "startup",
                                            "data_type": "analog"
                                        }
                                        total_found += 1
                                        _LOGGER.warning(f"✅ Found unknown analog sensor {sensor_key} at {object_type}:{instance} = {value}")  # Changed to WARNING
                            
                            # For multi-state inputs, only use as fallback for status indication
                            elif object_type == 13:
                                # Only accept if not already found as analog, or if analog data was invalid
                                existing_sensor = discovered_sensors.get(sensor_key)
                                if existing_sensor is None or existing_sensor.get("data_type") != "analog":
                                    if expected_range:
                                        min_val, max_val = expected_range
                                        if min_val <= value <= max_val:
                                            # For multi-state: avoid obvious status values like 1.0 unless it's the only option
                                            if value != 1.0 or sensor_key.startswith("leak"):  # Accept 1.0 for leak sensors
                                                # Only add if we don't have analog data for this sensor
                                                if existing_sensor is None:
                                                    discovered_sensors[sensor_key] = {
                                                        "value": value,
                                                        "object_type": object_type,
                                                        "instance": instance,
                                                        "discovery_time": "startup",
                                                        "data_type": "status"
                                                    }
                                                    total_found += 1
                                                    _LOGGER.warning(f"✅ Found wireless sensor {sensor_key} at {object_type}:{instance} = {value} (STATUS)")
                                                else:
                                                    _LOGGER.warning(f"⏭️ Skipping status data - analog data already found for {sensor_key}")
                                            else:
                                                _LOGGER.warning(f"⏭️ Skipping likely status value {value} for {sensor_key}")
                                        else:
                                            _LOGGER.warning(f"❌ Multi-state value {value} at {object_type}:{instance} outside range {expected_range}")
                                else:
                                    _LOGGER.warning(f"⏭️ Skipping {sensor_key} at {object_type}:{instance} - analog data already found")
                        else:
                            _LOGGER.warning(f"❌ Could not parse value from response for {sensor_key}")
                    else:
                        _LOGGER.warning(f"⚠️ No response for {sensor_key} at {object_type}:{instance}")
                        
                except Exception as e:
                    _LOGGER.warning(f"❌ Error scanning {object_type}:{instance}: {e}")
                    continue
        
        if discovered_sensors:
            analog_count = len([s for s in discovered_sensors.values() if s.get("data_type") == "analog"])
            status_count = len([s for s in discovered_sensors.values() if s.get("data_type") == "status"])
            _LOGGER.warning(f"🎯 Wireless sensor discovery complete: found {total_found} sensors ({analog_count} analog, {status_count} status)")
            # Cache the discovered sensors for future reference
            self._discovered_wireless_sensors = discovered_sensors
        else:
            _LOGGER.warning("⚠️ No wireless sensors discovered - they may be offline or not present")
            self._discovered_wireless_sensors = {}
        
        return discovered_sensors

    def get_wireless_sensor_values(self) -> dict[str, Any]:
        """Get current values from discovered wireless sensors."""
        wireless_data = {}
        # Return empty dict if no sensors are discovered
        if not hasattr(self, '_discovered_wireless_sensors') or not self._discovered_wireless_sensors:
            _LOGGER.debug("No discovered wireless sensors to read values from")
            return wireless_data
        _LOGGER.debug("Reading values from %d discovered wireless sensors", len(self._discovered_wireless_sensors))
        for sensor_key, sensor_info in self._discovered_wireless_sensors.items():
            try:
                object_type = sensor_info["object_type"]
                instance = sensor_info["instance"]
                _LOGGER.debug(f"Reading {sensor_key} at {object_type}:{instance}")
                packet = self._create_read_property_request(object_type, instance)
                response = self._send_request(packet, retries=3)
                if response:
                    _LOGGER.debug(f"Got response for {sensor_key}: {response.hex()}")
                    expected_range = self._get_expected_range(sensor_key)
                    value = None
                    if object_type == 0:
                        value = self._parse_wireless_sensor_response(response, sensor_key)
                        if value is not None:
                            _LOGGER.debug(f"Specialized parser succeeded for {sensor_key}: {value}")
                    if value is None:
                        if object_type == 0:
                            pattern_3e44 = b"\x3e\x44"
                            if pattern_3e44 in response:
                                pattern_start = response.find(pattern_3e44)
                                _LOGGER.debug(f"Found 3E44 pattern at position {pattern_start}")
                                if b"\x3f" in response[pattern_start:]:
                                    end_3f = response.find(b"\x3f", pattern_start + 1)
                                    _LOGGER.debug(f"Found 3F at position {end_3f}")
                                    between_bytes = response[pattern_start + 2 : end_3f]
                                    _LOGGER.debug(f"Bytes between 3E44 and 3F: {between_bytes.hex()} (length: {len(between_bytes)})")
                        value = self._parse_value(response, object_type, expected_range, sensor_key)
                    if value is not None:
                        wireless_data[sensor_key] = value
                        _LOGGER.debug(f"Successfully read wireless {sensor_key}: {value}")
                    else:
                        _LOGGER.debug(f"Could not parse value for {sensor_key} from response: {response.hex()}")
                else:
                    _LOGGER.debug(f"No response received for {sensor_key}")
            except Exception as e:
                _LOGGER.debug(f"Error reading wireless sensor {sensor_key}: {e}")
        # Add battery percentage if battery_voltage is available
        if "battery_voltage" in wireless_data:
            battery_voltage = wireless_data["battery_voltage"]
            battery_percentage = self.calculate_battery_percentage(battery_voltage)
            wireless_data["battery"] = battery_percentage
            _LOGGER.debug(f"Calculated battery percentage: {battery_percentage}% from {battery_voltage}V")
        if wireless_data:
            _LOGGER.debug(f"Successfully read {len(wireless_data)} wireless sensor values: {wireless_data}")
        else:
            _LOGGER.debug("No wireless sensor values could be read - may be due to sleep mode or signal issues")
        return wireless_data

    def read_wireless_sensors(self, force_read: bool = False) -> dict[str, Any]:
        """Read wireless sensor data with smart intermittent handling."""
        from datetime import datetime, timedelta
        
        # Check if we have recent wireless data cached (wireless sensors are intermittent)
        if hasattr(self, '_last_wireless_data') and hasattr(self, '_last_wireless_check') and not force_read:
            time_since_check = datetime.now() - self._last_wireless_check
            if time_since_check < timedelta(minutes=5):  # Use cached data for 5 minutes
                _LOGGER.debug("Using cached wireless data (sensors are intermittent)")
                return self._last_wireless_data
        
        # Only attempt to read wireless sensors occasionally to avoid spam (unless forced)
        if not hasattr(self, '_wireless_attempt_count'):
            self._wireless_attempt_count = 0
        
        self._wireless_attempt_count += 1
        
        # Try wireless read every 3rd attempt to increase chance of catching active sensors
        if not force_read and self._wireless_attempt_count % 3 != 0:
            _LOGGER.debug("Skipping wireless sensor check (intermittent sensors)")
            return getattr(self, '_last_wireless_data', {})
        
        _LOGGER.debug("Attempting wireless sensor read...")
        wireless_data = {}
        
        # Approach 1: Use discovered sensors if available
        if hasattr(self, '_discovered_wireless_sensors') and self._discovered_wireless_sensors:
            wireless_data = self.get_wireless_sensor_values()
            if wireless_data:
                _LOGGER.debug("Found wireless data from discovered sensors: %s", wireless_data)
            
        # Approach 2: Try to read wireless objects from the same hub device (fallback)
        if not wireless_data and (force_read or self._wireless_attempt_count % 6 == 0):  # Attempt less frequently
            wireless_from_hub = self._read_wireless_from_hub()
            if wireless_from_hub:
                wireless_data.update(wireless_from_hub)
                _LOGGER.debug("Found wireless data from hub objects: %s", wireless_from_hub)
        
        # Approach 3: Try to discover separate wireless devices if no data from hub (fallback)
        if not wireless_data and (force_read or self._wireless_attempt_count % 12 == 0):  # Attempt rarely
            discovered_wireless = self._discover_wireless_devices()
            wireless_data.update(discovered_wireless)
            if discovered_wireless:
                _LOGGER.debug("Found wireless data from separate devices: %s", discovered_wireless)
        
        # debugging for empty data
        if not wireless_data and force_read:
            _LOGGER.debug("No wireless data found despite force_read=True. This may indicate sensors are sleeping or offline.")
            
            # Try a full rescan if we're forcing a read and got no data
            try:
                _LOGGER.debug("Attempting full wireless sensor rescan...")
                rescan_result = self.discover_wireless_sensors()
                if rescan_result:
                    _LOGGER.debug("Rescan found: %s", rescan_result)
                    # Try to get values from the newly discovered sensors
                    for sensor_key, sensor_info in rescan_result.items():
                        wireless_data[sensor_key] = sensor_info.get("value")
            except Exception as err:
                _LOGGER.debug("Error during forced rescan: %s", err)
        
        # Add battery percentage if battery_voltage is available
        if "battery_voltage" in wireless_data:
            battery_voltage = wireless_data["battery_voltage"]
            battery_percentage = self.calculate_battery_percentage(battery_voltage)
            wireless_data["battery"] = battery_percentage
            _LOGGER.debug(f"Calculated battery percentage: {battery_percentage}% from {battery_voltage}V")
                
        # Cache the results
        self._last_wireless_data = wireless_data
        self._last_wireless_check = datetime.now()
        
        if wireless_data:
            _LOGGER.info(f"Found {len(wireless_data)} active wireless sensors")
        else:
            _LOGGER.debug("No wireless sensors responding - normal for battery-powered devices")
        
        return wireless_data
    
    def _read_wireless_from_hub(self) -> dict[str, Any]:
        """Try to read wireless sensor data as objects within the main hub."""
        wireless_data = {}
        
        # Method 1: Try the discovered wireless object instances (11-15)
        from .const import WIRELESS_SENSOR_OBJECTS
        for key, obj in WIRELESS_SENSOR_OBJECTS.items():
            try:
                packet = self._create_read_property_request(obj["type"], obj["instance"])
                response = self._send_request(packet)
                
                if response:
                    expected_range = self._get_expected_range(key)
                    value = self._parse_value(response, obj["type"], expected_range, key)
                    if value is not None:
                        wireless_data[key] = value
                        _LOGGER.debug(f"Read wireless {key} from hub: {value}")
                    else:
                        _LOGGER.debug(f"Failed to parse wireless {key} value from response")
                        
            except Exception as e:
                _LOGGER.debug(f"Error reading wireless {key} from hub: {e}")
        
        # Method 2: If no standard wireless data found, scan for possible wireless objects  
        if not wireless_data:
            _LOGGER.debug("No wireless data from standard instances - sensors may be offline/out of range")
            wireless_data = self._scan_for_wireless_objects()
        else:
            _LOGGER.info(f"Successfully read {len(wireless_data)} wireless sensors: {list(wireless_data.keys())}")
        
        # Handle intermittent wireless sensors
        if not wireless_data:
            _LOGGER.debug("No wireless sensors responding - they may be offline, out of range, or in sleep mode")
            
        return wireless_data
    
    def _scan_for_wireless_objects(self) -> dict[str, Any]:
        """Scan for wireless sensor objects in different instance ranges."""
        wireless_data = {}
        
        # Try different instance ranges where wireless sensors might be located
        wireless_candidates = [
            # Try analog inputs that might contain wireless data
            (0, 11), (0, 12), (0, 13), (0, 14), (0, 15),  # Standard wireless range
            (0, 16), (0, 17), (0, 18), (0, 19), (0, 20),  # Extended range
            (0, 100), (0, 101), (0, 102), (0, 103), (0, 104),  # High range
        ]
        
        sensor_names = ["leak1", "leak2", "temperature", "humidity", "battery_voltage"]
        
        for i, (obj_type, instance) in enumerate(wireless_candidates):
            if i < len(sensor_names):
                sensor_name = sensor_names[i]
                try:
                    packet = self._create_read_property_request(obj_type, instance)
                    response = self._send_request(packet)
                    
                    if response:
                        expected_range = self._get_expected_range(sensor_name)
                        value = self._parse_value(response, obj_type, expected_range, sensor_name)
                        if value is not None:
                            wireless_data[sensor_name] = value
                            _LOGGER.info(f"Found wireless {sensor_name} at instance {instance}: {value}")
                            
                except Exception as e:
                    _LOGGER.debug(f"Error scanning wireless instance {instance}: {e}")
        
        if wireless_data:
            _LOGGER.info(f"Wireless scan found: {wireless_data}")
        else:
            _LOGGER.debug("No wireless sensors found during scan")
            
        return wireless_data
    
    def _discover_wireless_devices(self) -> dict[str, Any]:
        """Discover wireless sensors as separate devices (fallback method)."""
        wireless_data = {}
        
        # This is a fallback method that tries to discover wireless sensors
        # as separate BACnet devices rather than objects within the main hub
        _LOGGER.debug("Attempting to discover wireless sensors as separate devices...")
        
        # For now, return empty dict as this method is not fully implemented
        # The main discovery happens in discover_wireless_sensors()
        return wireless_data
    

    def _read_data_from_device(self, device_id: int) -> Optional[dict[str, Any]]:
        """Read data from a specific BACnet device."""
        from .const import WIRELESS_SENSOR_OBJECTS
        
        device_data = {}
        
        for key, obj in WIRELESS_SENSOR_OBJECTS.items():
            try:
                packet = self._create_read_property_request(
                    object_type=obj["type"],
                    object_instance=obj["instance"],
                )
                response = self._send_request(packet)
                
                if response:
                    expected_range = self._get_expected_range(key)
                    value = self._parse_value(response, obj["type"], expected_range, key)
                    if value is not None:
                        device_data[key] = value
                        
            except Exception as e:
                _LOGGER.debug(f"Error reading from device {device_id}, obj {key}: {e}")
                
        return device_data if device_data else None

    def _get_expected_range(self, key: str) -> Optional[tuple[int, int]]:
        """Return the expected value range for a given sensor key."""
        ranges = {
            "leak1": (0.0, 1),       # 0.0 = dry, 1.0+ = wet (updated to accept 0.0)
            "leak2": (0.0, 1),       # 0.0 = dry, 1.0+ = wet (updated to accept 0.0)
            "temperature": (-50, 100), # Wider temperature range to match standalone test (-50 to 100°C)
            "humidity": (0, 150),     # Wider humidity range to match standalone test (0-150%)
            "battery_voltage": (0, 5), # Wider voltage range to match standalone test (0-5V)
        }
        return ranges.get(key)

    def get_all_status(self, force_wireless_read: bool = False) -> dict[str, Any]:
        """Get all system status."""
        water_status = self.read_water_status()
        valve_status = self.read_valve_status()
        device_info = self.read_device_info()
        wireless_data = self.read_wireless_sensors(force_read=force_wireless_read)

        return {
            "water": water_status,
            "valve": valve_status,
            "device": device_info,
            "wireless": wireless_data,
        }

    def get_wireless_sensor_status(self) -> dict[str, str]:
        """Get current status of wireless sensors - real data vs status indicators."""
        if not hasattr(self, '_discovered_wireless_sensors'):
            return {}
            
        status_report = {}
        for sensor_key, sensor_info in self._discovered_wireless_sensors.items():
            data_type = sensor_info.get('data_type', 'unknown')
            value = sensor_info.get('value')
            object_type = sensor_info.get('object_type')
            instance = sensor_info.get('instance')
            
            if data_type == 'analog':
                if sensor_key in ['temperature', 'humidity', 'battery_voltage']:
                    status_report[sensor_key] = f"REAL DATA: {value} (from {object_type}:{instance})"
                else:
                    status_report[sensor_key] = f"ACTIVE: {value} (from {object_type}:{instance})"
            elif data_type == 'status':
                status_report[sensor_key] = f"STATUS ONLY: {value} (from {object_type}:{instance}) - waiting for real data"
            else:
                status_report[sensor_key] = f"UNKNOWN: {value} (from {object_type}:{instance})"
                
        return status_report 

    def calculate_battery_percentage(self, voltage: float) -> int:
        """Calculate battery percentage from voltage.
        
        Based on the battery specs:
        - 3.3V = 100%
        - 2.2V = 15%
        """
        if voltage is None:
            return None
            
        if voltage >= 3.3:
            return 100
        elif voltage <= 2.2:
            return 15
        else:
            # Linear interpolation between 2.2V (15%) and 3.3V (100%)
            percentage = 15 + ((voltage - 2.2) / (3.3 - 2.2)) * 85
            return round(percentage) 