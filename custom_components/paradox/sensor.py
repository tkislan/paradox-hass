from typing import TYPE_CHECKING, override

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory

from .entity import ParadoxEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ParadoxConfigEntry, ParadoxCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ParadoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ParadoxFailedRequestsSensor(entry.runtime_data)])


class ParadoxFailedRequestsSensor(ParadoxEntity, SensorEntity):
    _attr_translation_key = "failed_requests"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: ParadoxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_failed_requests"

    # The count comes from the client, not from the polled status, and matters most
    # while the polls fail.
    @property
    @override
    def available(self) -> bool:
        return True

    @property
    @override
    def native_value(self) -> int:
        return self.coordinator.client.failed_requests

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.client.add_failure_listener(self.async_write_ha_state)
        )
