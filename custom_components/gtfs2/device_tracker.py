from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ICON, ICONS
from .coordinator import GTFSUpdateCoordinator

STALE_REFRESHES_BEFORE_PRUNE = 3


def _vehicle_display_name(
    route_type: int | None, route_short_name: str | None, fallback: str
) -> str:
    """Build a readable tracker name such as Tramvaj 8 or Autobus 501."""
    vehicle_type_name = {
        0: "Tramvaj",
        1: "Metro",
        2: "Vlak",
        3: "Autobus",
        4: "Lod",
        5: "Lanovka",
        6: "Lanovka",
        7: "Lanovka",
    }.get(route_type, "Spoj")
    line_name = str(route_short_name or "").strip()
    if line_name:
        return f"{vehicle_type_name} {line_name}"
    return fallback


def _prune_orphaned_registry_entities(
    entity_registry: er.EntityRegistry,
    config_entry: ConfigEntry,
    current_keys: set[str],
) -> None:
    """Remove stale tracker entities from the HA entity registry."""
    unique_prefix = f"{config_entry.entry_id}_vehicle_"
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        unique_id = registry_entry.unique_id or ""
        if not unique_id.startswith(unique_prefix):
            continue
        vehicle_key = unique_id[len(unique_prefix) :]
        if vehicle_key in current_keys:
            continue
        entity_registry.async_remove(registry_entry.entity_id)


def _prune_orphaned_registry_devices(
    device_registry: dr.DeviceRegistry,
    config_entry: ConfigEntry,
    current_keys: set[str],
) -> None:
    """Remove stale tracker devices from the HA device registry."""
    identifier_prefix = f"vehicle_{config_entry.entry_id}_"
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        matched_vehicle_key = None
        for identifier_domain, identifier_value in device_entry.identifiers:
            if identifier_domain != DOMAIN:
                continue
            if not identifier_value.startswith(identifier_prefix):
                continue
            matched_vehicle_key = identifier_value[len(identifier_prefix) :]
            break
        if matched_vehicle_key is None or matched_vehicle_key in current_keys:
            continue
        device_registry.async_remove_device(device_entry.id)


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
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    @callback
    def sync_entities() -> None:
        vehicles = coordinator.data.get("vehicle_positions", []) if coordinator.data else []
        current_keys = {
            str(vehicle.get("entity_key") or vehicle.get("vehicle_id") or vehicle.get("trip_id"))
            for vehicle in vehicles
            if vehicle.get("entity_key") or vehicle.get("vehicle_id") or vehicle.get("trip_id")
        }

        _prune_orphaned_registry_entities(entity_registry, config_entry, current_keys)
        _prune_orphaned_registry_devices(device_registry, config_entry, current_keys)

        for existing_key, tracker in list(entities.items()):
            if existing_key in current_keys:
                tracker.mark_present()
                continue

            tracker.mark_missing()
            if tracker.should_prune():
                if tracker.entity_id:
                    entity_registry.async_remove(tracker.entity_id)
                entities.pop(existing_key, None)

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

    _attr_has_entity_name = False

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
        self._missing_refreshes = 0
        self._refresh_vehicle_data()

    async def async_added_to_hass(self) -> None:
        """Register coordinator listener only after the entity is attached."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
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

    def _refresh_vehicle_data(self) -> None:
        vehicles = self.coordinator.data.get("vehicle_positions", []) if self.coordinator.data else []
        self._vehicle_data = next(
            (vehicle for vehicle in vehicles if str(vehicle.get("entity_key")) == self.vehicle_key),
            {},
        )
        if self._vehicle_data:
            self._missing_refreshes = 0
            route_type = self._vehicle_data.get("route_type")
            self._attr_icon = ICONS.get(route_type, ICON)
            fallback_name = (
                self._vehicle_data.get("vehicle_label")
                or self._vehicle_data.get("trip_id")
                or self.vehicle_key
            )
            self._attr_name = _vehicle_display_name(
                route_type,
                self._vehicle_data.get("route_short_name"),
                fallback_name,
            )
            self._attr_device_info = DeviceInfo(
                name=f"GTFS Vehicle {self.vehicle_key}",
                entry_type=DeviceEntryType.SERVICE,
                identifiers={(DOMAIN, f"vehicle_{self.config_entry.entry_id}_{self.vehicle_key}")},
                manufacturer="GTFS",
                model="Realtime vehicle",
            )
            self._attr_extra_state_attributes = {
                "trip_id": self._vehicle_data.get("trip_id"),
                "route_id": self._vehicle_data.get("route_id"),
                "route_short_name": self._vehicle_data.get("route_short_name"),
                "direction_id": self._vehicle_data.get("direction_id"),
                "vehicle_id": self._vehicle_data.get("vehicle_id"),
                "vehicle_label": self._vehicle_data.get("vehicle_label"),
                "stop_id": self._vehicle_data.get("stop_id"),
                "first_stop_id": self._vehicle_data.get("first_stop_id"),
                "first_stop_name": self._vehicle_data.get("first_stop_name"),
                "last_stop_id": self._vehicle_data.get("last_stop_id"),
                "last_stop_name": self._vehicle_data.get("last_stop_name"),
                "bearing": self._vehicle_data.get("bearing"),
                "speed": self._vehicle_data.get("speed"),
                "timestamp": self._vehicle_data.get("timestamp"),
                "gtfs_rt_updated_at": self.coordinator.data.get("gtfs_rt_updated_at") if self.coordinator.data else None,
            }
        else:
            self._attr_extra_state_attributes = {}

    def mark_present(self) -> None:
        """Reset stale tracking for a vehicle still present in the latest snapshot."""
        self._missing_refreshes = 0

    def mark_missing(self) -> None:
        """Mark a vehicle as temporarily missing from the latest snapshot."""
        self._missing_refreshes += 1
        self._vehicle_data = {}
        self._attr_extra_state_attributes = {}
        if self.hass is not None:
            self.async_write_ha_state()

    def should_prune(self) -> bool:
        """Remove trackers that stayed absent across multiple refreshes."""
        return self._missing_refreshes >= STALE_REFRESHES_BEFORE_PRUNE

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_vehicle_data()
        if self.hass is not None:
            self.async_write_ha_state()
