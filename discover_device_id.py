"""
BACnet Device ID Discovery - Lightweight Version
===============================================
This script is used to discover the device ID of the Linkbox HUB.
It is not used in the integration, but can be used to develkopment and testing purposes.
"""

import socket
import struct
from typing import List, Dict

class DeviceIDDiscovery:
    def __init__(self):
        self.bacnet_port = 47808
        self.invoke_id = 0
        
    def send_packet(self, ip: str, packet: bytes, timeout: float = 1.0) -> bytes:
        """Send packet and return response"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(packet, (ip, self.bacnet_port))
        response, _ = sock.recvfrom(1024)
        sock.close()
        return response
        
    def create_read_property_packet(self, device_id: int, property_id: int) -> bytes:
        """Create ReadProperty request packet"""
        # BVLC + NPDU + APDU headers
        packet = bytearray([0x81, 0x0a, 0x00, 0x00, 0x01, 0x04, 0x00, 0x01, 0x0c])
        
        # Object Identifier (Device Object, instance = device_id)
        object_id = (8 << 22) | device_id
        packet.extend([0x0c])
        packet.extend(struct.pack('>I', object_id))
        
        # Property Identifier
        packet.extend([0x19, property_id])
        
        # Update length
        packet[2:4] = struct.pack('>H', len(packet))
        return bytes(packet)

    def extract_device_info(self, response: bytes) -> Dict:
        """Extract device ID and name from response"""
        device_ids = []
        object_name = None
        
        # Extract device IDs from object identifiers
        for i in range(len(response) - 4):
            if response[i] == 0x0C:  # Object identifier tag
                obj_id_bytes = response[i+1:i+5]
                full_obj_id = int.from_bytes(obj_id_bytes, byteorder='big')
                instance_num = full_obj_id & 0x3FFFFF
                if 1 <= instance_num <= 4194303:
                    device_ids.append(instance_num)
        
        # Extract object name from string value
        for i in range(len(response) - 2):
            if response[i] == 0x75:  # String tag
                strlen = response[i+1]
                if i+2+strlen <= len(response):
                    name_bytes = response[i+2:i+2+strlen]
                    object_name = name_bytes.decode('ascii', errors='replace')
                    break
        
        return {"device_ids": list(set(device_ids)), "object_name": object_name}

    def verify_device_id(self, target_ip: str, device_id: int) -> bool:
        """Verify device ID works"""
        try:
            packet = self.create_read_property_packet(device_id, 77)  # Object Name
            response = self.send_packet(target_ip, packet, timeout=1.0)
            return len(response) > 6 and response[6] in [0x30, 0x60]  # Success or vendor-specific
        except:
            return False

    def discover_devices(self, target_ip: str) -> List[Dict]:
        """Main discovery function"""
        print(f"=== BACnet Device Discovery ===")
        print(f"Target: {target_ip}:{self.bacnet_port}")
        
        # Test packet for Object Name (property 77)
        test_packet = bytes.fromhex("810a001101040005010c0c023fffff194d")
        
        try:
            print("📤 Sending discovery packet...")
            response = self.send_packet(target_ip, test_packet, timeout=2.0)
            print(f"📥 Received: {response.hex()}")
            
            # Extract device info
            info = self.extract_device_info(response)
            device_ids = info["device_ids"]
            object_name = info["object_name"]
            
            if not device_ids:
                print("No device IDs found")
                return []
            
            print(f"🔍 Found {len(device_ids)} device ID(s): {device_ids}")
            if object_name:
                print(f"Device name: {object_name}")
            
            # Verify each device ID
            verified_devices = []
            for device_id in device_ids:
                print(f"  Testing device ID: {device_id}")
                if self.verify_device_id(target_ip, device_id):
                    print(f"Device ID {device_id} verified")
                    verified_devices.append({
                        "ip": target_ip,
                        "port": self.bacnet_port,
                        "device_id": device_id,
                        "object_name": object_name
                    })
                else:
                    print(f"Device ID {device_id} failed verification")
            
            return verified_devices
            
        except Exception as e:
            print(f"❌ Error: {e}")
            return []

def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python local_whois_discovery.py <target_ip>")
        print("Example: python local_whois_discovery.py 192.168.2.251")
        sys.exit(1)
    
    target_ip = sys.argv[1]
    discovery = DeviceIDDiscovery()
    devices = discovery.discover_devices(target_ip)
    
    if devices:
        print(f"\nDiscovery successful! Found {len(devices)} device(s):")
        for i, device in enumerate(devices, 1):
            print(f"\n   Device {i}:")
            print(f"     IP: {device['ip']}")
            print(f"     Port: {device['port']}")
            print(f"     Device ID: {device['device_id']}")
            if device.get('object_name'):
                print(f"     Object Name: {device['object_name']}")
    else:
        print("\nNo devices discovered")

if __name__ == "__main__":
    main() 