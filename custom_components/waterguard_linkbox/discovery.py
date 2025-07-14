"""Discovery functions for Waterguard Linkbox."""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Any, List, Dict

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

BROADCAST_PORT = 47808
DISCOVERY_TIMEOUT = 3


class DiscoveryTimeout(Exception):
    """Exception raised when discovery times out."""


class DeviceIDDiscovery:
    def __init__(self, host: str, port: int = BROADCAST_PORT):
        self.host = host
        self.port = port

    def _create_discovery_packet(self) -> bytes:
        return bytes.fromhex("810a001101040005010c0c023fffff194d")

    def _send_packet_sync(self, packet: bytes, timeout: float = 2.0) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (self.host, self.port))
            response, _ = sock.recvfrom(1024)
            return response
        finally:
            sock.close()

    def _extract_all_device_ids(self, response: bytes) -> list[int]:
        device_ids = []
        
        try:
            # Extract device IDs from object identifiers
            for i in range(len(response) - 4):
                if response[i] == 0x0C:  # Object identifier tag
                    obj_id_bytes = response[i+1:i+5]
                    if len(obj_id_bytes) == 4:
                        full_obj_id = int.from_bytes(obj_id_bytes, byteorder='big')
                        object_type = full_obj_id >> 22
                        instance_num = full_obj_id & 0x3FFFFF
                        
                        # Only accept Device objects (type 8) with valid instance numbers
                        if object_type == 8 and 1 <= instance_num <= 4194303:
                            if instance_num not in device_ids:
                                device_ids.append(instance_num)
                                _LOGGER.debug(f"Found device ID: {instance_num}")
        except Exception as e:
            _LOGGER.debug(f"Error while parsing device IDs: {e}")
            
        return device_ids

    def _verify_device_id(self, device_id: int) -> bool:
        """Verify device ID works by trying to read object name.
        
        This matches the working script's verification logic.
        """
        try:
            # Create ReadProperty request for Object Name (property 77)
            packet = self._create_read_property_packet(device_id, 77)
            response = self._send_packet_sync(packet, timeout=1.0)
            
            # Check if we got a valid response (not an error)
            return len(response) > 6 and response[6] in [0x30, 0x60]  # Success or vendor-specific
        except Exception:
            return False

    def _create_read_property_packet(self, device_id: int, property_id: int) -> bytes:
        """Create ReadProperty request packet for verification."""
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

    async def discover_devices(self) -> list[int]:
        """Discover device IDs using the same approach as the working script."""
        _LOGGER.info(f"Starting device discovery for {self.host}:{self.port}")
        
        try:
            # Create discovery packet
            packet = self._create_discovery_packet()
            _LOGGER.debug(f"Sending discovery packet: {packet.hex()}")
            
            # Send packet and get response (run in executor to avoid blocking)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._send_packet_sync, packet, DISCOVERY_TIMEOUT
            )
            
            _LOGGER.debug(f"Received response: {response.hex()}")
            
            # Extract device IDs
            device_ids = self._extract_all_device_ids(response)
            
            if not device_ids:
                _LOGGER.info("No device IDs found in response")
                return []
            
            _LOGGER.info(f"Found {len(device_ids)} device ID(s): {device_ids}")
            
            # Verify each device ID
            verified_devices = []
            for device_id in device_ids:
                _LOGGER.info(f"Verifying device ID: {device_id}")
                is_valid = await loop.run_in_executor(
                    None, self._verify_device_id, device_id
                )
                
                if is_valid:
                    verified_devices.append(device_id)
                    _LOGGER.info(f"✅ Device ID {device_id} verified successfully")
                else:
                    _LOGGER.warning(f"❌ Device ID {device_id} failed verification")
            
            if verified_devices:
                _LOGGER.info(f"Successfully verified {len(verified_devices)} device ID(s): {verified_devices}")
            else:
                _LOGGER.warning("No device IDs could be verified")
                
            return verified_devices
            
        except socket.timeout:
            _LOGGER.info(f"Discovery timed out for {self.host}:{self.port}")
            raise DiscoveryTimeout(f"Discovery timed out for {self.host}:{self.port}")
        except Exception as e:
            _LOGGER.error(f"Error during discovery for {self.host}:{self.port}: {e}")
            raise DiscoveryTimeout(f"Discovery failed for {self.host}:{self.port}: {e}")


async def async_discover_device_ids(hass: HomeAssistant, host: str, port: int) -> list[int]:
    """Discover device IDs for a specific host."""
    _LOGGER.info(f"Discovering device IDs for {host}:{port}")
    
    try:
        discovery = DeviceIDDiscovery(host, port)
        device_ids = await discovery.discover_devices()
        
        if device_ids:
            _LOGGER.info(f"Successfully discovered device IDs: {device_ids}")
            return device_ids
        else:
            _LOGGER.info(f"No device IDs discovered for {host}:{port}")
            return []
            
    except DiscoveryTimeout as e:
        _LOGGER.info(f"Discovery timeout: {e}")
        raise
    except Exception as e:
        _LOGGER.error(f"Unexpected error during discovery: {e}")
        raise DiscoveryTimeout(f"Discovery failed: {e}")


async def async_discover_hubs(hass: HomeAssistant | None = None) -> list[dict[str, Any]]:
    """Scan the network for Waterguard hubs."""
    # This function is not currently used for config flow, but is kept for potential future use.
    return [] 