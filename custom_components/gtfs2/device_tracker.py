from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ICON, ICONS
from .coordinator import GTFSUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GTFS realtime vehicle trackers."""
    coordinator: GTFSUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]

    entities: dict[str, GTFSVehicleTracker] = {}

    @callback
    def sync_entities() -> None:
        vehicles = coordinator.data.get("vehicle_positions", []) if coordinator.data else []
        new_entities = []
        for vehicle in vehicles:
            vehicle_key = str(vehicle.get("entity_key") or vehicle.get("vehicle_id") or vehicle.get("trip_id"))
            if not vehicle_key or vehicle_key in entities:
                continue
            tracker = GTFSVehicleTracker(coordinator, config_entry, vehicle_key)
            entities[vehicle_key] = tracker
            new_entities.append(tracker)
        if new_entities:
            async_add_entities(new_entities)

    sync_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(sync_entities))


class GTFSVehicleTracker(TrackerEntity):
    """Device tracker for one GTFS realtime vehicle."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GTFSUpdateCoordinator,
        config_entry: ConfigEntry,
        vehicle_key: str,
    ) -> None:
        self.coordinator = coordinator
        self.config_entry = config_entry
        self.vehicle_key = vehicle_key
        self._attr_unique_id = f"{config_entry.entry_id}_vehicle_{vehicle_key}"
        self._attr_name = vehicle_key
        self._attr_icon = ICON
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self._attr_device_info = DeviceInfo(
            name=f"GTFS Vehicle {vehicle_key}",
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"vehicle_{config_entry.entry_id}_{vehicle_key}")},
            manufacturer="GTFS",
            model="Realtime vehicle",
        )
        self._vehicle_data: dict[str, Any] = {}
        self.async_on_remove(coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return bool(self._vehicle_data)

    @property
    def latitude(self):
        return self._vehicle_data.get("latitude")

    @property
    def longitude(self):
        return self._vehicle_data.get("longitude")

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def location_name(self):
        return self._vehicle_data.get("stop_id")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attr_extra_state_attributes

    @callback
    def _handle_coordinator_update(self) -> None:
        vehicles = self.coordinator.data.get("vehicle_positions", []) if self.coordinator.data else []
        self._vehicle_data = next(
            (vehicle for vehicle in vehicles if str(vehicle.get("entity_key")) == self.vehicle_key),
            {},
        )
        if self._vehicle_data:
            route_type = self._vehicle_data.get("route_type")
            self._attr_icon = ICONS.get(route_type, ICON)
            self._attr_name = self._vehicle_data.get("vehicle_label") or self._vehicle_data.get("trip_id") or self.vehicle_key
            self._attr_extra_state_attributes = {
                "trip_id": self._vehicle_data.get("trip_id"),
                "route_id": self._vehicle_data.get("route_id"),
                "direction_id": self._vehicle_data.get("direction_id"),
                "vehicle_id": self._vehicle_data.get("vehicle_id"),
                "vehicle_label": self._vehicle_data.get("vehicle_label"),
                "stop_id": self._vehicle_data.get("stop_id"),
                "bearing": self._vehicle_data.get("bearing"),
                "speed": self._vehicle_data.get("speed"),
                "timestamp": self._vehicle_data.get("timestamp"),
                "gtfs_rt_updated_at": self.coordinator.data.get("gtfs_rt_updated_at") if self.coordinator.data else None,
            }
        self.async_write_ha_state()
