from typing import TYPE_CHECKING, Final, override

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .entity import ParadoxEntity, async_remove_stale_entities
from .panel.errors import ParadoxError
from .panel.models import PanelCommand

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ParadoxConfigEntry, ParadoxCoordinator
    from .panel.models import PanelStatus

PARALLEL_UPDATES = 1

# Keyed by the area state codes of the panel's tbl_useraccess; ready (8) and not ready
# (9) both count as disarmed.
AREA_STATES: Final = {
    1: AlarmControlPanelState.DISARMED,
    2: AlarmControlPanelState.ARMED_AWAY,
    3: AlarmControlPanelState.TRIGGERED,
    4: AlarmControlPanelState.ARMED_NIGHT,
    5: AlarmControlPanelState.ARMED_HOME,
    6: AlarmControlPanelState.PENDING,
    7: AlarmControlPanelState.ARMING,
    8: AlarmControlPanelState.DISARMED,
    9: AlarmControlPanelState.DISARMED,
    10: AlarmControlPanelState.ARMED_HOME,
}


def area_state(status: PanelStatus, index: int) -> AlarmControlPanelState | None:
    if index in status.alarms:
        return AlarmControlPanelState.TRIGGERED
    code = status.area_code(index)
    return None if code is None else AREA_STATES.get(code)


def _area_unique_id(entry: ParadoxConfigEntry, index: int) -> str:
    return f"{entry.entry_id}_area_{index}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ParadoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    indexes = [
        index for index, code in enumerate(coordinator.data.useraccess) if code != 0
    ]
    async_add_entities(ParadoxAreaPanel(coordinator, index) for index in indexes)
    async_remove_stale_entities(
        hass,
        entry,
        Platform.ALARM_CONTROL_PANEL,
        {_area_unique_id(entry, index) for index in indexes},
    )


class ParadoxAreaPanel(ParadoxEntity, AlarmControlPanelEntity):
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )
    _attr_code_arm_required = False

    def __init__(self, coordinator: ParadoxCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = _area_unique_id(coordinator.config_entry, index)
        names = coordinator.layout.area_names
        name = names[index].strip() if index < len(names) else ""
        self._attr_name = name or f"Area {index + 1}"

    @property
    @override
    def alarm_state(self) -> AlarmControlPanelState | None:
        return area_state(self.coordinator.data, self._index)

    @override
    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._async_send(PanelCommand.ARM)

    @override
    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._async_send(PanelCommand.STAY)

    @override
    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._async_send(PanelCommand.SLEEP)

    @override
    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._async_send(PanelCommand.DISARM)

    async def _async_send(self, command: PanelCommand) -> None:
        try:
            await self.coordinator.client.async_command(self._index, command)
        except ParadoxError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"area": str(self.name)},
            ) from err
        await self.coordinator.async_request_refresh()
