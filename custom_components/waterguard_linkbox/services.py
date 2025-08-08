"""Services for Waterguard Linkbox."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import WaterguardDataUpdateCoordinator
from .wireless_diagnostic import WirelessDiagnostic

_LOGGER = logging.getLogger(__name__)

SERVICE_RUN_DIAGNOSTICS = "run_diagnostics"

SERVICE_RUN_DIAGNOSTICS_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("include_raw_data", default=False): cv.boolean,
        vol.Optional("test_wireless", default=True): cv.boolean,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for Waterguard Linkbox."""
    
    async def run_diagnostics_service(call: ServiceCall) -> None:
        """Run comprehensive diagnostics service."""
        try:
            device_id = call.data.get("device_id")
            include_raw_data = call.data.get("include_raw_data", False)
            test_wireless = call.data.get("test_wireless", True)
            
            _LOGGER.info("Starting diagnostic service for device_id: %s", device_id)
            
            # Find the coordinator
            coordinator: WaterguardDataUpdateCoordinator | None = None
            for entry_id, coordinator_instance in hass.data[DOMAIN].items():
                if device_id is None or coordinator_instance.hub.device_id == device_id:
                    coordinator = coordinator_instance
                    break
            
            if coordinator is None:
                _LOGGER.error("No coordinator found for device_id: %s", device_id)
                return
            
            diagnostic_results = {
                "timestamp": str(coordinator.data.get("timestamp", "Unknown")),
                "last_update": "Connected" if coordinator.last_update_success else "Disconnected",
                "connection_status": "connected" if coordinator.last_update_success else "disconnected",
                "hub_info": {
                    "ip": coordinator.hub.host,
                    "port": coordinator.hub.port,
                    "device_id": coordinator.hub.device_id,
                },
                "data_analysis": {},
                "recommendations": [],
                "raw_data": coordinator.data if include_raw_data else None,
            }
            
            # Analyze current data
            data = coordinator.data or {}
            
            # Water system analysis
            water_data = data.get("water", {})
            diagnostic_results["data_analysis"]["water"] = {
                "alarm": water_data.get("alarm", "No data"),
                "leak1": water_data.get("leak1", "No data"),
                "status": "OK" if water_data.get("alarm", 0) == 0 and water_data.get("leak1", 0) == 0 else "ALARM",
            }
            
            # Valve system analysis
            valve_data = data.get("valve", {})
            num_valves = valve_data.get("num_valves", 0)
            diagnostic_results["data_analysis"]["valve"] = {
                "num_valves": num_valves,
                "valve_status1": valve_data.get("valve_status1", "No data"),
                "valve_status2": valve_data.get("valve_status2", "No data"),
                "valve_control": valve_data.get("valve_control", "No data"),
                "issues": [],
            }
            
            # Valve diagnostics
            if num_valves == 4.0:
                diagnostic_results["data_analysis"]["valve"]["issues"].append(
                    "Invalid valve count (4.0) - Expected 0-3, this suggests BACnet parsing issue"
                )
            
            if valve_data.get("valve_status1") == 1:
                diagnostic_results["data_analysis"]["valve"]["issues"].append(
                    "Valve 1 status is 'Unknown' (1) - may indicate disconnected valve"
                )
            
            if valve_data.get("valve_status2") == 1 and num_valves > 1:
                diagnostic_results["data_analysis"]["valve"]["issues"].append(
                    "Valve 2 status is 'Unknown' (1) - may indicate disconnected valve"
                )
            
            # Wireless sensor analysis
            wireless_data = data.get("wireless", {})
            diagnostic_results["data_analysis"]["wireless"] = {
                "temperature": wireless_data.get("temperature", "No data"),
                "humidity": wireless_data.get("humidity", "No data"),
                "battery_voltage": wireless_data.get("battery_voltage", "No data"),
                "leak_sensor1": wireless_data.get("leak_sensor1", "No data"),
                "leak_sensor2": wireless_data.get("leak_sensor2", "No data"),
                "status": "Available" if any(v not in [None, "No data"] for v in wireless_data.values()) else "Unavailable",
            }
            
            if all(v in [None, "No data"] for v in wireless_data.values()):
                diagnostic_results["recommendations"].append(
                    "All wireless sensors unavailable - check wireless connectivity or sensor battery"
                )
            
            # Test wireless if requested
            if test_wireless:
                try:
                    wireless_diag = WirelessDiagnostic(coordinator.hub)
                    
                    wireless_test_result = await wireless_diag.run_comprehensive_test(hass)
                    diagnostic_results["wireless_test"] = {
                        "test_performed": True,
                        "comprehensive_results": wireless_test_result,
                        "status": wireless_test_result["overall_status"],
                        "success_rate": wireless_test_result["summary"]["success_rate"],
                    }
                except Exception as err:
                    diagnostic_results["wireless_test"] = {
                        "test_performed": True,
                        "error": str(err),
                        "status": "Error",
                    }
            
            # Add cache and state machine information
            cache_stats = coordinator.get_cache_stats()
            state_info = coordinator.get_state_machine_info()
            
            diagnostic_results["cache_analysis"] = {
                "total_entities": len(cache_stats),
                "total_readings": sum(cache_stats.values()),
                "entity_breakdown": cache_stats,
                "status": "Healthy" if len(cache_stats) > 0 else "Empty",
            }
            
            diagnostic_results["state_machine"] = {
                "alarm_state": state_info.get("alarm_state", "unknown"),
                "valve_state": state_info.get("valve_state", "unknown"),
                "force_sync_needed": state_info.get("should_force_sync", False),
                "status": "Synchronized" if not state_info.get("should_force_sync", False) else "Needs Sync",
            }
            
            # Add cache-related recommendations
            if len(cache_stats) == 0:
                diagnostic_results["recommendations"].append(
                    "No cached data available - this may indicate connectivity issues"
                )
            
            if state_info.get("should_force_sync", False):
                diagnostic_results["recommendations"].append(
                    "State machine indicates valve synchronization needed - consider manual valve control"
                )
            
            # Log results
            _LOGGER.info("=== Waterguard Linkbox Diagnostics ===")
            _LOGGER.info("Connection Status: %s", diagnostic_results['connection_status'])
            _LOGGER.info("Water System: %s", diagnostic_results['data_analysis']['water']['status'])
            _LOGGER.info("Valve Issues: %d", len(diagnostic_results['data_analysis']['valve']['issues']))
            _LOGGER.info("Wireless Status: %s", diagnostic_results['data_analysis']['wireless']['status'])
            _LOGGER.info("Cache Status: %s (%d entities, %d readings)", 
                        diagnostic_results['cache_analysis']['status'],
                        diagnostic_results['cache_analysis']['total_entities'],
                        diagnostic_results['cache_analysis']['total_readings'])
            _LOGGER.info("State Machine: %s (Alarm: %s, Valve: %s)", 
                        diagnostic_results['state_machine']['status'],
                        diagnostic_results['state_machine']['alarm_state'],
                        diagnostic_results['state_machine']['valve_state'])
            
            if diagnostic_results.get('wireless_test'):
                _LOGGER.info("\nWireless Test Results:")
                _LOGGER.info("  Status: %s", diagnostic_results['wireless_test']['status'])
                _LOGGER.info("  Success Rate: %.1f%%", diagnostic_results['wireless_test']['success_rate'])

            _LOGGER.info("\nCheck logs for complete diagnostic results.")
            
            # Create persistent notification with results
            wireless_info = ""
            if test_wireless and "wireless_test" in diagnostic_results:
                wireless_test = diagnostic_results["wireless_test"]
                if wireless_test["status"] != "Error":
                    wireless_info = f"""
**Wireless Test Results:**
• Status: {wireless_test['status']}
• Success Rate: {wireless_test.get('success_rate', 0):.1f}%
"""
            
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": f"{DOMAIN}_diagnostics",
                    "title": "🔍 Waterguard Linkbox Diagnostics",
                    "message": f"""
**Connection Status:** {diagnostic_results['connection_status']}
**Water System:** {diagnostic_results['data_analysis']['water']['status']}
**Valve Issues:** {len(diagnostic_results['data_analysis']['valve']['issues'])}
**Wireless Status:** {diagnostic_results['data_analysis']['wireless']['status']}
**Cache Status:** {diagnostic_results['cache_analysis']['status']} ({diagnostic_results['cache_analysis']['total_entities']} entities, {diagnostic_results['cache_analysis']['total_readings']} readings)
**State Machine:** {diagnostic_results['state_machine']['status']} (Alarm: {diagnostic_results['state_machine']['alarm_state']}, Valve: {diagnostic_results['state_machine']['valve_state']})
{wireless_info}
Check logs for complete diagnostic results.
""",
                },
                blocking=False,
            )
            
        except Exception as err:
            _LOGGER.error("Error running diagnostics: %s", err)
            # Create error notification
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": f"{DOMAIN}_diagnostics_error",
                    "title": "❌ Waterguard Diagnostics Error",
                    "message": f"Failed to run diagnostics: {err}\n\nCheck logs for more details.",
                },
                blocking=False,
            )
    
    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_DIAGNOSTICS,
        run_diagnostics_service,
        schema=SERVICE_RUN_DIAGNOSTICS_SCHEMA,
    )
    
    _LOGGER.info("Waterguard Linkbox services registered")


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload services."""
    hass.services.async_remove(DOMAIN, SERVICE_RUN_DIAGNOSTICS)
    _LOGGER.info("Waterguard Linkbox services unregistered") 