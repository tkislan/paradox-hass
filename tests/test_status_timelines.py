"""Replay the zone and area-0 timelines of the bridge's status_machine.json."""

from itertools import groupby
from typing import TYPE_CHECKING, Any, Final

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform

from tests.fake_panel import ZONE_SLOTS, FakeResponse, Reset
from tests.helpers import entity_id_for, setup_entry, tick
from tests.spec_tables import spec_rows, spec_text

if TYPE_CHECKING:
    from _pytest.mark import ParameterSet
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel

STATUS_PATH: Final = "/statuslive.html"
GROUPS: Final = (
    "first_poll",
    "armed_codes",
    "sensor_codes",
    "transitions",
    "zone_indexing",
    "failures",
)
ROW_KEYS: Final = {"zones", "polls", "expected_events"}
ARMED_BY_STATE: Final = {
    "armed_away": True,
    "armed_home": True,
    "armed_night": True,
    "arming": True,
    "pending": True,
    "disarmed": False,
    "unknown": None,
}

type Row = dict[str, Any]
type Histories = dict[int, list[str]]
type Armed = list[bool | None]


ROWS: Final[list[tuple[str, Row]]] = [
    (group, param.values[0])
    for group in GROUPS
    for param in spec_rows("status_machine", group, ROW_KEYS)
]


def _param(group: str, row: Row) -> ParameterSet:
    return pytest.param(row, id=f"{group}/{row['name']}")


FAILED_FIRST_POLL: Final = [
    _param(group, row) for group, row in ROWS if "fail" in row["polls"][0]
]
# No row of the bridge's table starts on a code outside the table for a used area.
# This one checks that such an area still gets an entity, in the unknown state.
UNKNOWN_FIRST_POLL: Final[Row] = {
    "name": "unknown on the first poll is not announced",
    "zones": [[1, "A"]],
    "polls": [
        {"statuszone": [0, 0], "useraccess": [11, 0]},
        {"statuszone": [0, 0], "useraccess": [2, 0]},
    ],
    "expected_events": [["armedChanged", True]],
}
SERVED_FIRST_POLL: Final = [
    *(_param(group, row) for group, row in ROWS if "fail" not in row["polls"][0]),
    _param("local", UNKNOWN_FIRST_POLL),
]


def serve(panel: FakePanel, poll: Row) -> None:
    match poll:
        case {"fail": {"connection": _}}:
            # aiohttp resends a GET whose connection dropped before any response, so
            # one poll reaches the panel twice.
            panel.respond_with(STATUS_PATH, Reset(), times=2)
        case {"fail": {"page": page}}:
            panel.respond_with(STATUS_PATH, FakeResponse(body=spec_text(page)), times=1)
        case {"fail": {"status": status} as fail}:
            panel.respond_with(
                STATUS_PATH,
                FakeResponse(status=status, body=fail.get("body") or "error"),
                times=1,
            )
        case {"page": page}:
            panel.respond_with(STATUS_PATH, FakeResponse(body=spec_text(page)), times=1)
        case {"statuszone": _} | {"useraccess": _}:
            panel.set_status(
                statuszone=poll.get("statuszone"), useraccess=poll.get("useraccess")
            )
        case _:
            raise ValueError(f"Unknown poll {poll}")


def zone_entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[int, str]:
    ids = {
        slot: entity_id_for(hass, entry, Platform.BINARY_SENSOR, f"zone_{slot}")
        for slot in range(ZONE_SLOTS)
    }
    return {slot: entity_id for slot, entity_id in ids.items() if entity_id}


def zone_states(hass: HomeAssistant, entity_ids: dict[int, str]) -> dict[int, str]:
    states = {
        slot: hass.states.get(entity_id) for slot, entity_id in entity_ids.items()
    }
    assert None not in states.values(), f"Entities without a state: {entity_ids}"
    return {slot: state.state for slot, state in states.items()}


def area_0_state(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if entity_id is None:
        return None
    state = hass.states.get(entity_id)
    assert state is not None, f"Entity without a state: {entity_id}"
    return state.state


def normalised(states: list[str]) -> list[str]:
    """Drop ``unavailable``, repeats and the closed state the bridge assumed first."""
    changes = [state for state, _ in groupby(s for s in states if s != "unavailable")]
    return changes[1:] if changes[:1] == ["off"] else changes


def armed_history(states: list[str | None]) -> Armed:
    """
    Map area states to armedChanged values, dropping ``unavailable`` and repeats.

    The unknown state the bridge assumes first is never announced, so it is dropped.
    """
    flags = [
        ARMED_BY_STATE[state]
        for state in states
        if state is not None and state != "unavailable"
    ]
    changes = [flag for flag, _ in groupby(flags)]
    return changes[1:] if changes[:1] == [None] else changes


def expected_histories(row: Row) -> Histories:
    histories: Histories = {
        slot: [] for slot, (bitmask, _) in enumerate(row["zones"]) if bitmask
    }
    for event in row["expected_events"]:
        if event[0] == "sensorChanged":
            _, slot, is_open = event
            histories[int(slot)].append("on" if is_open else "off")
    return histories


def expected_armed(row: Row) -> Armed:
    return [event[1] for event in row["expected_events"] if event[0] == "armedChanged"]


async def replay(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    entry: MockConfigEntry,
    row: Row,
) -> tuple[Histories, Armed]:
    panel.zones = [(bitmask, name) for bitmask, name in row["zones"]]
    first, *later = row["polls"]
    serve(panel, first)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    entity_ids = zone_entity_ids(hass, entry)
    area_id = entity_id_for(hass, entry, Platform.ALARM_CONTROL_PANEL, "area_0")

    observed = [zone_states(hass, entity_ids)]
    area_observed = [area_0_state(hass, area_id)]
    for poll in later:
        serve(panel, poll)
        await tick(hass, freezer)
        states = zone_states(hass, entity_ids)
        unavailable = {slot for slot, s in states.items() if s == "unavailable"}
        assert unavailable == (set(entity_ids) if "fail" in poll else set())
        observed.append(states)

        area = area_0_state(hass, area_id)
        assert area is None or (area == "unavailable") == ("fail" in poll)
        area_observed.append(area)

    histories = {
        slot: normalised([states[slot] for states in observed]) for slot in entity_ids
    }
    return histories, armed_history(area_observed)


@pytest.mark.parametrize("row", SERVED_FIRST_POLL)
async def test_zone_and_area_states_follow_the_polls(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    row: Row,
) -> None:
    histories, armed = await replay(hass, freezer, panel, mock_config_entry, row)

    assert histories == expected_histories(row)
    assert armed == expected_armed(row)


@pytest.mark.parametrize("row", FAILED_FIRST_POLL)
async def test_setup_retries_when_the_first_poll_fails(
    hass: HomeAssistant,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    row: Row,
) -> None:
    panel.zones = [(bitmask, name) for bitmask, name in row["zones"]]
    serve(panel, row["polls"][0])

    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
