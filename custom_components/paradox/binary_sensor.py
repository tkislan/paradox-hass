from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory, Platform

from .entity import ParadoxEntity, async_remove_stale_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ParadoxConfigEntry, ParadoxCoordinator

PARALLEL_UPDATES = 0


def _zone_unique_id(entry: ParadoxConfigEntry, slot: int) -> str:
    return f"{entry.entry_id}_zone_{slot}"


def _trouble_unique_id(entry: ParadoxConfigEntry) -> str:
    return f"{entry.entry_id}_trouble"


def _trouble_name(names: tuple[str, ...], index: int) -> str:
    name = names[index].strip() if 0 <= index < len(names) else ""
    return name or f"Trouble {index}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ParadoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    slots = coordinator.layout.zone_slots
    async_add_entities(
        [
            *(ParadoxZoneSensor(coordinator, slot) for slot in slots),
            ParadoxTroubleSensor(coordinator),
        ]
    )
    async_remove_stale_entities(
        hass,
        entry,
        Platform.BINARY_SENSOR,
        {_zone_unique_id(entry, slot) for slot in slots} | {_trouble_unique_id(entry)},
    )


class ParadoxZoneSensor(ParadoxEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: ParadoxCoordinator, slot: int) -> None:
        super().__init__(coordinator)
        self._slot = slot
        self._attr_unique_id = _zone_unique_id(coordinator.config_entry, slot)
        name = coordinator.layout.zones[slot][1].strip()
        self._attr_name = name or f"Zone {slot + 1}"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.zone_open(self._slot)


class ParadoxTroubleSensor(ParadoxEntity, BinarySensorEntity):
    _attr_translation_key = "trouble"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ParadoxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = _trouble_unique_id(coordinator.config_entry)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.troubles)

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        names = self.coordinator.layout.trouble_names
        return {
            "troubles": [
                _trouble_name(names, index) for index in self.coordinator.data.troubles
            ]
        }
