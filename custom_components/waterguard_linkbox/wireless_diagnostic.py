"""Wireless sensor diagnostic utilities for Waterguard Linkbox."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from .const import WIRELESS_SENSOR_OBJECTS, DOMAIN
from .hub import WaterguardLinkboxHub

_LOGGER = logging.getLogger(__name__)


class WirelessDiagnostic:
    """Diagnostic tools for wireless sensor connectivity."""
    
    def __init__(self, hub: WaterguardLinkboxHub) -> None:
        """Initialize wireless diagnostic."""
        self.hub = hub
        self.last_test_results: Dict[str, Any] = {}
        self.connectivity_history: List[Dict[str, Any]] = []
        
    async def run_comprehensive_test(self, hass: HomeAssistant) -> Dict[str, Any]:
        """Run comprehensive wireless sensor diagnostic test."""
        test_results = {
            "timestamp": utcnow(),
            "overall_status": "unknown",
            "sensor_tests": {},
            "connectivity_analysis": {},
            "raw_responses": {},
        }
        
        # Test 1: Basic connectivity test
        _LOGGER.info("Starting wireless sensor connectivity test...")
        
        successful_reads = 0
        failed_reads = 0
        
        for sensor_key, sensor_obj in WIRELESS_SENSOR_OBJECTS.items():
            test_results["sensor_tests"][sensor_key] = await self._test_individual_sensor(
                sensor_key, sensor_obj, hass
            )
            
            if test_results["sensor_tests"][sensor_key]["status"] == "success":
                successful_reads += 1
            else:
                failed_reads += 1
        
        # Test 2: Connection patterns
        test_results["connectivity_analysis"] = await self._analyze_connectivity_patterns(hass)
        
        # Determine status
        if successful_reads == 0:
            test_results["overall_status"] = "no_connectivity"
        elif successful_reads == len(WIRELESS_SENSOR_OBJECTS):
            test_results["overall_status"] = "full_connectivity"
        else:
            test_results["overall_status"] = "partial_connectivity"
        
        test_results["summary"] = {
            "successful_reads": successful_reads,
            "failed_reads": failed_reads,
            "total_sensors": len(WIRELESS_SENSOR_OBJECTS),
            "success_rate": (successful_reads / len(WIRELESS_SENSOR_OBJECTS)) * 100,
        }
        
        self.last_test_results = test_results
        self._update_connectivity_history(test_results)
        
        return test_results
    
    async def _test_individual_sensor(self, sensor_key: str, sensor_obj: Dict[str, Any], hass: HomeAssistant) -> Dict[str, Any]:
        """Test connectivity to individual wireless sensor."""
        sensor_test = {
            "sensor_key": sensor_key,
            "object_type": sensor_obj["type"],
            "object_instance": sensor_obj["instance"],
            "status": "unknown",
            "value": None,
            "error": None,
            "response_time": None,
            "raw_response": None,
        }
        
        try:
            start_time = datetime.now()
            
            # Try to read the sensor directly
            packet = self.hub._create_read_property_request(
                sensor_obj["type"], sensor_obj["instance"]
            )
            response = await hass.async_add_executor_job(
                self.hub._send_request, packet
            )
            
            end_time = datetime.now()
            sensor_test["response_time"] = (end_time - start_time).total_seconds()
            
            if response:
                sensor_test["raw_response"] = response.hex() if response else None
                
                # Try to parse the value
                expected_range = self._get_expected_range(sensor_key)
                value = self.hub._parse_value(response, sensor_obj["type"], expected_range)
                
                if value is not None:
                    sensor_test["value"] = value
                    sensor_test["status"] = "success"
                else:
                    sensor_test["status"] = "parse_error"
                    sensor_test["error"] = "Could not parse valid value from response"
            else:
                sensor_test["status"] = "no_response"
                sensor_test["error"] = "No response from sensor"
                
        except Exception as err:
            sensor_test["status"] = "error"
            sensor_test["error"] = str(err)
            
        return sensor_test
    
    async def _analyze_connectivity_patterns(self, hass: HomeAssistant) -> Dict[str, Any]:
        """Analyze wireless connectivity patterns."""
        analysis = {
            "hub_wireless_support": "unknown",
            "device_discovery": [],
            "signal_strength": "unknown",
            "interference_analysis": "unknown",
        }
        
        # Test 1: Check if hub supports wireless at all
        try:
            coordinator: WaterguardDataUpdateCoordinator | None = None
            for coordinator_instance in hass.data[DOMAIN].values():
                if coordinator_instance.hub.device_id == self.hub.device_id:
                    coordinator = coordinator_instance
                    break
            
            if coordinator and coordinator.data and any(key.startswith("wireless_") for key in coordinator.data):
                analysis["hub_wireless_support"] = "supported"
            else:
                analysis["hub_wireless_support"] = "not_supported"

        except Exception as err:
            analysis["hub_wireless_support"] = f"error: {err}"
        
        # Test 2: Try to discover wireless devices
        if analysis["hub_wireless_support"] == "supported":
            analysis["device_discovery"] = await self._discover_wireless_devices(hass)
        
        return analysis
    
    async def _discover_wireless_devices(self, hass: HomeAssistant) -> List[Dict[str, Any]]:
        """Attempt to discover wireless devices using Who-Is broadcast."""
        discovered_devices = []
        
        # Use the same discovery mechanism as the config flow
        try:
            from .discovery import async_discover_hubs, DiscoveryTimeout
            
            # Run discovery for a short period
            discovered_hubs = await async_discover_hubs(hass)
            
            for hub in discovered_hubs:
                # Filter for wireless devices (typically different device IDs)
                if hub.get("device_id") != self.hub.device_id:
                    discovered_devices.append({
                        "device_id": hub.get("device_id"),
                        "host": hub.get("host"),
                        "port": hub.get("port"),
                        "status": "found_via_discovery",
                    })

        except DiscoveryTimeout:
            _LOGGER.warning("Wireless discovery timed out")
        except Exception as err:
            _LOGGER.error("Error during wireless discovery: %s", err)
            
        return discovered_devices
    
    def _get_expected_range(self, sensor_key: str) -> Optional[tuple]:
        """Get expected range for sensor values."""
        ranges = {
            "leak1": (0, 1),
            "leak2": (0, 1),
            "temperature": (-40, 85),
            "humidity": (0, 100),
            "battery_voltage": (1.5, 4.0),
        }
        return ranges.get(sensor_key)
    
    def _update_connectivity_history(self, test_results: Dict[str, Any]) -> None:
        """Update connectivity history."""
        history_entry = {
            "timestamp": test_results["timestamp"],
            "overall_status": test_results["overall_status"],
            "success_rate": test_results["summary"]["success_rate"],
        }
        
        self.connectivity_history.append(history_entry)
        
        # Keep only last 24 hours of history
        cutoff_time = utcnow() - timedelta(hours=24)
        self.connectivity_history = [
            entry for entry in self.connectivity_history
            if entry["timestamp"] > cutoff_time
        ]
    
    def get_connectivity_trend(self) -> Dict[str, Any]:
        """Get connectivity trend analysis."""
        if not self.connectivity_history:
            return {"trend": "no_data", "analysis": "No historical data available"}
        
        recent_history = self.connectivity_history[-10:]  # Last 10 entries
        
        if len(recent_history) < 2:
            return {"trend": "insufficient_data", "analysis": "Need more data points"}
        
        # Calculate trend
        success_rates = [entry["success_rate"] for entry in recent_history]
        avg_recent = sum(success_rates[-3:]) / 3 if len(success_rates) >= 3 else success_rates[-1]
        avg_older = sum(success_rates[:-3]) / max(1, len(success_rates) - 3)
        
        if avg_recent > avg_older + 10:
            trend = "improving"
        elif avg_recent < avg_older - 10:
            trend = "declining"
        else:
            trend = "stable"
        
        return {
            "trend": trend,
            "current_rate": success_rates[-1],
            "average_rate": sum(success_rates) / len(success_rates),
            "analysis": f"Connectivity trend: {trend} (current: {success_rates[-1]:.1f}%)",
        } 