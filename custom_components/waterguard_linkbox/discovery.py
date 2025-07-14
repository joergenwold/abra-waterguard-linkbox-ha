"""Discovery functions for Waterguard Linkbox."""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

BROADCAST_PORT = 47808
DISCOVERY_TIMEOUT = 5


class DiscoveryError(Exception):
    """Custom exception for discovery errors."""


class DiscoveryTimeout(Exception):
    """Custom exception for discovery timeout."""


async def async_discover_hubs(hass: HomeAssistant | None = None) -> list[dict[str, Any]]:
    """Scan the network for Waterguard hubs."""
    _LOGGER.info("Starting Waterguard hub discovery")

    discovered_hubs = []
    transport = None

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)

    # Get the running asyncio loop
    loop = asyncio.get_running_loop()

    try:
        # BACnet "Who-Is" request
        who_is_packet = b"\x81\x0b\x00\x0c\x01\x20\xff\xff\x00\xff\x10\x08"
        broadcast_address = "255.255.255.255"

        # Create a datagram endpoint
        transport, _ = await loop.create_datagram_endpoint(
            lambda: DiscoveryProtocol(discovered_hubs),
            sock=sock,
        )

        # Send the broadcast packet
        transport.sendto(who_is_packet, (broadcast_address, BROADCAST_PORT))
        _LOGGER.debug(f"Sent Who-Is broadcast to {broadcast_address}:{BROADCAST_PORT}")

        # Wait for responses
        await asyncio.sleep(DISCOVERY_TIMEOUT)

    except Exception as e:
        _LOGGER.error(f"Error during discovery setup: {e}")
        raise DiscoveryError from e
    finally:
        if transport:
            transport.close()

    if not discovered_hubs:
        _LOGGER.info("No Waterguard hubs found during discovery.")
    else:
        _LOGGER.info(f"Discovered {len(discovered_hubs)} hubs.")

    return discovered_hubs


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol for handling discovery responses."""
    def __init__(self, discovered_hubs: list):
        self.discovered_hubs = discovered_hubs

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        """Handle received datagram."""
        host, _ = addr
        _LOGGER.debug(f"Received response from {host}")
        
        # Basic validation for an "I-Am" response
        if len(data) > 12 and data[4:8] == b'\x01\x00\x00\x00':
            try:
                # Extract device ID from I-Am packet
                device_id_bytes = data[12:16]
                device_id = int.from_bytes(device_id_bytes, byteorder='big')
                
                # Extract port from the response data if possible, otherwise use default
                # This is a placeholder, as the port isn't typically in the I-Am response
                port = BROADCAST_PORT

                hub_info = {
                    "host": host,
                    "port": port,
                    "device_id": device_id,
                }

                # Avoid duplicates
                if hub_info not in self.discovered_hubs:
                    self.discovered_hubs.append(hub_info)
                    _LOGGER.info(f"Discovered hub: {hub_info}")

            except Exception as e:
                _LOGGER.debug(f"Error parsing I-Am response from {host}: {e}")

    def error_received(self, exc: Exception):
        """Handle protocol error."""
        _LOGGER.error(f"Discovery protocol error: {exc}")

    def connection_lost(self, exc: Exception | None):
        """Handle connection lost."""
        _LOGGER.debug("Discovery socket closed.") 