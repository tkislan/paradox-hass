import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
    Platform,
)
from homeassistant.core import Event, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.paradox.const import DOMAIN
from tests.fake_panel import (
    FakePanel,
    FakeResponse,
    PanelRequest,
    PanelResult,
    Reset,
    render_index_page,
    render_login_page,
)
from tests.helpers import (
    TROUBLE_ENTITY_ID,
    advance,
    entity_id_for,
    hang_once,
    setup_entry,
    tick,
)
from tests.logs import problem_records

if TYPE_CHECKING:
    from collections.abc import Mapping

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

PACKAGE_LOGGER: Final = "custom_components.paradox"
STATUS: Final = "GET /statuslive.html"
KEEP_ALIVE: Final = "GET /keep_alive.html?msgid=1"
LOGOUT: Final = "GET /logout.html"

ZONE_ENTITY_IDS: Final = (
    "binary_sensor.paradox_predsien",
    "binary_sensor.paradox_lava_izba",
    "binary_sensor.paradox_prava_izba",
    "binary_sensor.paradox_spalna",
    "binary_sensor.paradox_obyvacka",
    "binary_sensor.paradox_chodba",
)
SLOT_0_OPEN: Final = [1, *[0] * 31]


def zone_states(hass: HomeAssistant) -> dict[str, str]:
    return {
        state.entity_id: state.state
        for state in hass.states.async_all("binary_sensor")
        if state.entity_id != TROUBLE_ENTITY_ID
    }


def zone_registry_entries(hass: HomeAssistant, entry_id: str) -> list[er.RegistryEntry]:
    return [
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry_id
        )
        if registry_entry.domain == Platform.BINARY_SENSOR
        and registry_entry.entity_id != TROUBLE_ENTITY_ID
    ]


def problems(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """List the warnings and errors, except the notice about a custom integration."""
    return [r for r in problem_records(caplog) if r.name != "homeassistant.loader"]


def tracebacks(caplog: pytest.LogCaptureFixture) -> list[str]:
    """List the problems logged with a traceback, which are the unexpected ones."""
    return [r.getMessage() for r in problems(caplog) if r.exc_info]


def record_state_writes(hass: HomeAssistant) -> list[Event]:
    events: list[Event] = []

    @callback
    def record(event: Event) -> None:
        events.append(event)

    @callback
    def any_entity(_data: Mapping[str, Any]) -> bool:
        return True

    hass.bus.async_listen(EVENT_STATE_CHANGED, record)
    hass.bus.async_listen(EVENT_STATE_REPORTED, record, event_filter=any_entity)
    return events


async def test_setup_creates_a_motion_sensor_for_each_zone_in_use(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)

    entry_id = mock_config_entry.entry_id
    registry_entries = zone_registry_entries(hass, entry_id)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert {entry.entity_id: entry.unique_id for entry in registry_entries} == {
        "binary_sensor.paradox_predsien": f"{entry_id}_zone_0",
        "binary_sensor.paradox_lava_izba": f"{entry_id}_zone_1",
        "binary_sensor.paradox_prava_izba": f"{entry_id}_zone_2",
        "binary_sensor.paradox_spalna": f"{entry_id}_zone_3",
        "binary_sensor.paradox_obyvacka": f"{entry_id}_zone_4",
        "binary_sensor.paradox_chodba": f"{entry_id}_zone_5",
    }
    assert {entry.original_device_class for entry in registry_entries} == {"motion"}
    assert {entry.has_entity_name for entry in registry_entries} == {True}
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "off")
    state = hass.states.get("binary_sensor.paradox_lava_izba")
    assert state is not None
    assert state.attributes["friendly_name"] == "Paradox Lava izba"


async def test_zone_entities_share_one_paradox_device(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    # A title other than "Paradox" tells the device name from the manufacturer.
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home alarm",
        data={"host": panel.host, "username": "user", "password": "1234"},
    )

    await setup_entry(hass, entry)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].identifiers == {(DOMAIN, entry.entry_id)}
    assert devices[0].name == "Home alarm"
    assert devices[0].manufacturer == "Paradox"
    assert devices[0].configuration_url == f"http://{panel.host}/"
    registry_entries = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    assert {registry_entry.device_id for registry_entry in registry_entries} == {
        devices[0].id
    }


async def test_unused_zone_slots_get_no_entity_and_the_rest_keep_their_slot(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.zones = [(1, "Hall"), (0, " "), (2, "Garage")]
    panel.set_status(statuszone=[0, 0, 1, *[0] * 29])

    await setup_entry(hass, mock_config_entry)

    entry_id = mock_config_entry.entry_id
    registry_entries = zone_registry_entries(hass, entry_id)
    assert {entry.entity_id: entry.unique_id for entry in registry_entries} == {
        "binary_sensor.paradox_hall": f"{entry_id}_zone_0",
        "binary_sensor.paradox_garage": f"{entry_id}_zone_2",
    }
    assert zone_states(hass) == {
        "binary_sensor.paradox_hall": "off",
        "binary_sensor.paradox_garage": "on",
    }


async def test_zone_with_a_blank_name_is_named_after_its_slot_number(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.zones = [(1, "Hall"), (1, "Kitchen"), (1, "Garage"), (1, "   ")]

    await setup_entry(hass, mock_config_entry)

    zone_4 = entity_id_for(hass, mock_config_entry, Platform.BINARY_SENSOR, "zone_3")
    assert zone_4 == "binary_sensor.paradox_zone_4"
    state = hass.states.get("binary_sensor.paradox_zone_4")
    assert state is not None
    assert state.attributes["friendly_name"] == "Paradox Zone 4"


async def test_panel_gets_a_poll_every_second_and_a_keep_alive_every_three(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen_at_setup = len(panel.requests)

    await tick(hass, freezer, 30)

    requests = panel.request_lines[seen_at_setup:]
    assert requests.count(STATUS) == 30
    assert requests.count(KEEP_ALIVE) == 10
    assert len(requests) == 40


async def test_zone_entities_write_state_only_when_the_panel_changes(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    writes = record_state_writes(hass)

    await tick(hass, freezer, 30)

    assert writes == []

    panel.set_status(statuszone=SLOT_0_OPEN)
    await tick(hass, freezer)

    changed = [event for event in writes if event.event_type == EVENT_STATE_CHANGED]
    assert [event.data["entity_id"] for event in changed] == [ZONE_ENTITY_IDS[0]]
    assert changed[0].data["new_state"].state == "on"


@pytest.mark.parametrize(
    "answer",
    [FakeResponse(body=render_login_page("AB12")), Reset()],
    ids=["login page", "reset"],
)
async def test_failed_keep_alive_is_only_logged_at_debug_level(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    *,
    answer: PanelResult,
) -> None:
    caplog.set_level(logging.DEBUG, logger=PACKAGE_LOGGER)
    await setup_entry(hass, mock_config_entry)
    panel.respond_with("/keep_alive.html", answer)

    await tick(hass, freezer, 3)

    keep_alive_logs = [
        record.levelno
        for record in caplog.records
        if record.getMessage().startswith("Keep-alive failed")
    ]
    assert keep_alive_logs == [logging.DEBUG]
    assert problems(caplog) == []
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "off")


async def test_keep_alive_still_running_when_the_next_is_due_starts_no_second_request(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    arrived = hang_once(panel, "/keep_alive.html")
    await tick(hass, freezer, 2)
    await advance(hass, freezer, 1)
    await arrived.wait()

    await advance(hass, freezer, 9)
    panel.release()
    await hass.async_block_till_done(wait_background_tasks=True)

    # The one that hung, and aiohttp's resend of it when the panel dropped it.
    assert len(panel.requests_to("/keep_alive.html")) == 2


async def test_failed_poll_makes_zones_unavailable_until_the_next_good_poll(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    panel.set_status(statuszone=SLOT_0_OPEN)
    await setup_entry(hass, mock_config_entry)
    restored = {**dict.fromkeys(ZONE_ENTITY_IDS, "off"), ZONE_ENTITY_IDS[0]: "on"}
    assert zone_states(hass) == restored
    seen_at_setup = len(panel.requests)

    panel.respond_with("/statuslive.html", Reset())
    await tick(hass, freezer)

    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "unavailable")
    assert tracebacks(caplog) == []

    panel.clear_overrides()
    await tick(hass, freezer)

    assert zone_states(hass) == restored
    assert "GET /login_page.html" not in panel.request_lines[seen_at_setup:]


async def test_unloading_the_entry_logs_the_panel_out_and_stops_all_activity(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=PACKAGE_LOGGER)
    await setup_entry(hass, mock_config_entry)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id) is True

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert panel.request_lines[-1] == LOGOUT
    assert {state.state for state in hass.states.async_all("binary_sensor")} == {
        "unavailable"
    }
    seen_at_unload = len(panel.requests)
    caplog.clear()
    await tick(hass, freezer, 10)
    assert len(panel.requests) == seen_at_unload
    assert [r for r in caplog.records if r.name.startswith(PACKAGE_LOGGER)] == []


async def test_failed_platform_unload_leaves_the_panel_session_open(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen_at_setup = len(panel.requests)

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        unloaded = await hass.config_entries.async_unload(mock_config_entry.entry_id)

    assert unloaded is False
    assert mock_config_entry.state is ConfigEntryState.FAILED_UNLOAD
    assert LOGOUT not in panel.request_lines[seen_at_setup:]


async def test_home_assistant_stop_logs_the_panel_out_and_stops_all_requests(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert panel.request_lines[-1] == LOGOUT
    seen_at_stop = len(panel.requests)
    await tick(hass, freezer, 10)
    assert len(panel.requests) == seen_at_stop


async def test_unloading_after_home_assistant_stop_logs_no_problem(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, mock_config_entry)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id) is True

    assert problems(caplog) == []


async def test_unloading_while_the_logout_hangs_completes_at_the_request_deadline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, mock_config_entry)
    arrived = hang_once(panel, "/logout.html")
    unload = asyncio.create_task(
        hass.config_entries.async_unload(mock_config_entry.entry_id)
    )
    await arrived.wait()

    await advance(hass, freezer, 10)

    assert not unload.done()

    await advance(hass, freezer, 11)

    assert await unload is True
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert problems(caplog) == []


async def test_poll_that_meets_a_closed_client_keeps_the_last_data(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    panel.set_status(statuszone=SLOT_0_OPEN)
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    arrived = hang_once(panel, "/keep_alive.html")
    await tick(hass, freezer, 2)
    await advance(hass, freezer, 1)
    await arrived.wait()
    # The client sends one request at a time, so the poll due now waits behind the
    # hung keep-alive, and the client is closed before that poll has its turn.
    await advance(hass, freezer, 1)
    seen_at_hang = len(panel.requests)
    unload = asyncio.create_task(
        hass.config_entries.async_unload(mock_config_entry.entry_id)
    )

    panel.release()
    assert await unload is True

    assert panel.request_lines[seen_at_hang:] == [KEEP_ALIVE, LOGOUT]
    assert coordinator.last_update_success
    assert coordinator.data.zone_open(0)
    assert problems(caplog) == []


async def test_rejected_credentials_start_reauth_and_leave_the_panel_logged_out(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Paradox",
        data={"host": panel.host, "username": "user", "password": "0000"},
    )

    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [(flow["handler"], flow["context"]["source"]) for flow in flows] == [
        (DOMAIN, "reauth")
    ]
    assert flows[0]["context"]["entry_id"] == entry.entry_id
    assert panel.request_lines[-1] == LOGOUT


async def test_finishing_reauth_with_accepted_credentials_reloads_the_entry(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Paradox",
        data={"host": panel.host, "username": "user", "password": "0000"},
    )
    await setup_entry(hass, entry)
    [flow] = hass.config_entries.flow.async_progress()

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"username": "user", "password": "1234"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert dict(entry.data) == {
        "host": panel.host,
        "username": "user",
        "password": "1234",
    }
    assert entry.state is ConfigEntryState.LOADED
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "off")
    assert hass.config_entries.flow.async_progress() == []


async def test_unreachable_panel_retries_the_setup(
    hass: HomeAssistant, refused_host: str, caplog: pytest.LogCaptureFixture
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Paradox",
        data={"host": refused_host, "username": "user", "password": "1234"},
    )

    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert zone_states(hass) == {}
    assert tracebacks(caplog) == []


async def test_first_poll_failure_retries_the_setup_and_logs_the_panel_out(
    hass: HomeAssistant,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    panel.respond_with("/statuslive.html", FakeResponse(status=500, body="error"))

    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert panel.request_lines[-1] == LOGOUT
    assert zone_states(hass) == {}
    assert tracebacks(caplog) == []


async def test_home_assistant_stop_during_the_login_retries_the_setup_after_logging_out(
    hass: HomeAssistant,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def stop(_request: PanelRequest) -> PanelResult:
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        return FakeResponse(body=render_index_page(panel.zones))

    panel.respond_with("/index.html", stop, times=1)

    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert panel.request_lines[-1] == LOGOUT
    assert zone_states(hass) == {}
    assert tracebacks(caplog) == []


async def test_failed_platform_setup_logs_the_panel_out(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(side_effect=RuntimeError),
    ):
        await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert panel.request_lines[-1] == LOGOUT


async def test_zone_taken_out_of_use_disappears_after_a_reload(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.zones = panel.zones[:4]

    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    registry_entries = zone_registry_entries(hass, mock_config_entry.entry_id)
    assert {entry.entity_id for entry in registry_entries} == set(ZONE_ENTITY_IDS[:4])
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS[:4], "off")


async def test_stale_zone_cleanup_leaves_the_entities_of_other_platforms_alone(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)
    sensor = er.async_get(hass).async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{mock_config_entry.entry_id}_area_0",
        config_entry=mock_config_entry,
    )

    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert er.async_get(hass).async_get(sensor.entity_id) is not None
