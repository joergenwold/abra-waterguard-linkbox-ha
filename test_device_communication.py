# This script is used to test the parsing methods with  device responses.
# It is not used in the integration, but can be used to test the parsing methods.

import socket
import struct
import math
import time

# Device info
DEVICE_IP = "xxx.xxx.x.xxx" # Replace with your Linkbox IP address
DEVICE_PORT = 47808
DEVICE_ID = 1 # Replace with your Linkbox ID
LOCAL_PORT = 47809  # Can be any available UDP port

# BACnet object definitions
OBJECTS = [
    (0, 7,   "Water Alarm", "alarm", (0, 1)),
    (0, 9,   "Wired Leak1", "leak1", (0, 1)),
    (14, 10, "Reset Leak", "reset_leak", None),
    (13, 3,  "Num Valves", "num_valves", None),
    (13, 5,  "Valve Status 1", "valve_status1", None),
    (13, 6,  "Valve Status 2", "valve_status2", None),
    (14, 1,  "Valve Control", "valve_control", None),
    (0, 11,  "Wireless Leak1", "leak1", None),
    (0, 12,  "Wireless Leak2", "leak2", None),
    (0, 13,  "Wireless Temperature", "temperature", None),
    (0, 14,  "Wireless Humidity", "humidity", None),
    (0, 15,  "Wireless Battery Voltage", "battery_voltage", None),
]

# BACnet ReadProperty request builder
def create_read_property_request(object_type, object_instance, property_id=85):
    bvlc_header = struct.pack(">BBH", 0x81, 0x0A, 0)
    npdu_header = struct.pack(">BB", 0x01, 0x04)
    invoke_id = 1
    apdu_header = struct.pack(">BBB", 0x00, 0x00, invoke_id)
    service_choice = bytes([0x0C])
    obj_id = (object_type << 22) | object_instance
    obj_identifier = b"\x0C" + struct.pack(">L", obj_id)
    # Property Identifier (unsigned)
    if property_id < 254:
        prop_id = b"\x19" + bytes([property_id])
    elif property_id < 65536:
        prop_id = b"\x19" + bytes([254, (property_id >> 8) & 0xFF, property_id & 0xFF])
    else:
        prop_id = b"\x19" + bytes([255, (property_id >> 16) & 0xFF, (property_id >> 8) & 0xFF, property_id & 0xFF])
    packet = bvlc_header + npdu_header + apdu_header + service_choice + obj_identifier + prop_id
    packet = packet[:2] + struct.pack(">H", len(packet)) + packet[4:]
    return packet

# UDP send/receive
def send_bacnet_request(packet, timeout=2.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        sock.bind(("0.0.0.0", LOCAL_PORT))
        sock.sendto(packet, (DEVICE_IP, DEVICE_PORT))
        data, addr = sock.recvfrom(1024)
        return data
    except socket.timeout:
        return None
    finally:
        sock.close()

# Parsing logic (mimics v1 hub.py)
def parse_value(data, object_type=None, expected_range=None, sensor_key=None):
    try:
        if not data or len(data) < 4:
            return None
        # Water sensor special format
        if (len(data) == 23 and 
            data[:4] == b'\x81\x0a\x00\x17' and 
            data[4:8] == b'\x01\x00\x30\x01' and
            data[16:19] == b'\x3e\x44\x3f'):
            if len(data) >= 20:
                value_byte = data[19]
                if value_byte == 0x00:
                    return 0.0
                elif value_byte == 0x80:
                    return 1.0
                else:
                    return 0.0
            return 0.0
        # BACnet error bit (except for water sensor special case)
        if (data[3] & 0x02) != 0:
            if (len(data) >= 4 and data[3] == 0x17):
                pass
            else:
                return None
        # Wireless sensor format (3E44 pattern)
        if object_type == 0 and len(data) >= 22 and data[:4] == b'\x81\x0a\x00\x17' and data[16:18] == b'\x3e\x44':
            float_bytes = data[18:22]
            try:
                value = struct.unpack(">f", float_bytes)[0]
                # Strict leak logic
                if sensor_key in ["leak1", "leak2"]:
                    if value == 1.0:
                        return 1.0
                    elif value == 0.0:
                        return 0.0
                    else:
                        return 0.0
                elif sensor_key == "temperature":
                    if -50 <= value <= 100:
                        return value
                    else:
                        return None
                elif sensor_key == "humidity":
                    if 0 <= value <= 100:
                        return value
                    else:
                        return None
                elif sensor_key == "battery_voltage":
                    if 1.5 <= value <= 4.0:
                        return value
                    else:
                        return None
                else:
                    if not (math.isnan(value) or math.isinf(value)):
                        return value
                    else:
                        return None
            except struct.error:
                return None
        # Enumerated value (0x91)
        if b"\x91" in data:
            enum_start = data.find(b"\x91")
            if enum_start >= 0 and enum_start + 1 < len(data):
                value = float(data[enum_start + 1])
                return value
        # Unsigned integer (0x21)
        if b"\x21" in data:
            uint_start = data.find(b"\x21")
            if uint_start >= 0 and uint_start + 1 < len(data):
                single_byte_value = data[uint_start + 1]
                if uint_start + 3 <= len(data):
                    two_byte_value = (data[uint_start + 1] << 8) | data[uint_start + 2]
                    if two_byte_value in [319, 1087]:
                        return float(two_byte_value)
                return float(single_byte_value)
        # Boolean value (0x10)
        if b"\x10" in data:
            bool_start = data.find(b"\x10")
            if bool_start >= 0 and bool_start + 1 < len(data):
                value = float(data[bool_start + 1])
                return value
        # Standard real value (0x44)
        if b"\x44" in data:
            real_start = data.find(b"\x44")
            if real_start >= 0 and real_start + 5 <= len(data):
                real_bytes = data[real_start + 1 : real_start + 5]
                try:
                    value = struct.unpack(">f", real_bytes)[0]
                    if expected_range:
                        min_val, max_val = expected_range
                        if min_val <= value <= max_val:
                            return value
                        else:
                            return None
                    else:
                        if -100 <= value <= 10000:
                            return value
                        else:
                            return None
                except struct.error:
                    return None
        return None
    except Exception:
        return None

def main():
    print("Waterguard Linkbox Standalone BACnet Test")
    print("=" * 50)
    for object_type, instance, name, sensor_key, expected_range in OBJECTS:
        print(f"\n--- {name} (type={object_type}, instance={instance}) ---")
        packet = create_read_property_request(object_type, instance)
        response = send_bacnet_request(packet)
        if response:
            print(f"Raw BACnet response: {response.hex()}")
            parsed = parse_value(response, object_type, expected_range, sensor_key)
            print(f"Parsed value: {parsed}")
        else:
            print("No response received.")
        time.sleep(0.5)
    print("\nCompleted!")

if __name__ == "__main__":
    main() 