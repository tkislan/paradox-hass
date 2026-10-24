from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ParadoxConfigEntry, ParadoxCoordinator


class ParadoxEntity(CoordinatorEntity[ParadoxCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ParadoxCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            name=entry.title,
            configuration_url=f"http://{entry.data[CONF_HOST]}/",
        )


@callback
def async_remove_stale_entities(
    hass: HomeAssistant,
    entry: ParadoxConfigEntry,
    platform: Platform,
    unique_ids: set[str],
) -> None:
    """Remove the entities of ``platform`` whose unique ID is not in ``unique_ids``."""
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registry_entry.domain == platform
            and registry_entry.unique_id not in unique_ids
        ):
            registry.async_remove(registry_entry.entity_id)
