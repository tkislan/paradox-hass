from typing import TYPE_CHECKING, Final

import pytest
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.paradox.const import DOMAIN
from custom_components.paradox.panel.errors import (
    ParadoxConnectionError,
    ParadoxHttpError,
    ParadoxSessionExpiredError,
)
from tests.fake_panel import (
    FakeResponse,
    PanelRequest,
    PanelResult,
    Reset,
    render_index_page,
    render_login_page,
)
from tests.helpers import setup_entry, tick

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel

STATUS: Final = "GET /statuslive.html"
AREA_0: Final = "alarm_control_panel.paradox_cast_1"
AREA_1: Final = "alarm_control_panel.paradox_cast_2"
AREA_ENTITY_IDS: Final = (AREA_0, AREA_1)
INDEX_AREA_NAMES: Final = 'new Array("Cast 1","Cast 2")'

ACTIONS: Final = [
    pytest.param("alarm_arm_away", "r", 1, "disarmed", "armed_away", id="arm away"),
    pytest.param("alarm_arm_home", "s", 1, "disarmed", "armed_home", id="arm home"),
    pytest.param("alarm_arm_night", "p", 1, "disarmed", "armed_night", id="arm night"),
    pytest.param("alarm_disarm", "d", 2, "armed_away", "disarmed", id="disarm"),
]
FAILURES: Final = [
    pytest.param(
        FakeResponse(status=500, body="error"),
        1,
        ParadoxHttpError,
        "Request failed with status code 500",
        id="http 500",
    ),
    # aiohttp resends a GET whose connection dropped before any response, so the panel
    # sees the command twice.
    pytest.param(
        Reset(), 2, ParadoxConnectionError, "ServerDisconnectedError", id="reset"
    ),
    pytest.param(
        FakeResponse(body=render_login_page("AB12")),
        1,
        ParadoxSessionExpiredError,
        "Command answered with the login page",
        id="login page",
    ),
]


def is_command(request: PanelRequest) -> bool:
    return "value" in request.query


def command_lines(panel: FakePanel) -> list[str]:
    return [line for line in panel.request_lines if "value=" in line]


def alarm_unique_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    registry_entries = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    return {
        registry_entry.entity_id: registry_entry.unique_id
        for registry_entry in registry_entries
        if registry_entry.domain == Platform.ALARM_CONTROL_PANEL
    }


def alarm_states(hass: HomeAssistant) -> dict[str, str]:
    return {
        state.entity_id: state.state
        for state in hass.states.async_all("alarm_control_panel")
    }


def original_names(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        unique_id: registry.async_get(entity_id).original_name
        for entity_id, unique_id in alarm_unique_ids(hass, entry).items()
    }


async def call(hass: HomeAssistant, service: str, entity_id: str) -> None:
    await hass.services.async_call(
        "alarm_control_panel", service, {"entity_id": entity_id}, blocking=True
    )


def serve_area_names(panel: FakePanel, table: str) -> None:
    page = render_index_page(panel.zones).replace(INDEX_AREA_NAMES, table)
    panel.respond_with("/index.html", FakeResponse(body=page))


async def test_setup_creates_an_alarm_panel_for_the_area_in_use(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert alarm_unique_ids(hass, mock_config_entry) == {
        AREA_0: f"{mock_config_entry.entry_id}_area_0"
    }
    assert original_names(hass, mock_config_entry) == {
        f"{mock_config_entry.entry_id}_area_0": "Cast 1"
    }
    state = hass.states.get(AREA_0)
    assert state is not None
    assert state.state == "disarmed"
    assert state.attributes["friendly_name"] == "Paradox Cast 1"
    assert state.attributes["supported_features"] == (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )
    assert state.attributes["code_arm_required"] is False
    assert state.attributes["code_format"] is None
    registry_entry = er.async_get(hass).async_get(AREA_0)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, mock_config_entry.entry_id), mock_config_entry.entry_id
    )
    assert registry_entry is not None
    assert device is not None
    assert registry_entry.device_id == device.id


async def test_setup_creates_an_alarm_panel_for_each_area_in_use(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.set_status(useraccess=[1, 2])

    await setup_entry(hass, mock_config_entry)

    entry_id = mock_config_entry.entry_id
    assert alarm_unique_ids(hass, mock_config_entry) == {
        AREA_0: f"{entry_id}_area_0",
        AREA_1: f"{entry_id}_area_1",
    }
    assert alarm_states(hass) == {AREA_0: "disarmed", AREA_1: "armed_away"}


async def test_area_with_code_0_gets_no_entity_and_the_rest_keep_their_index(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.set_status(useraccess=[0, 1])

    await setup_entry(hass, mock_config_entry)

    assert alarm_unique_ids(hass, mock_config_entry) == {
        AREA_1: f"{mock_config_entry.entry_id}_area_1"
    }
    assert alarm_states(hass) == {AREA_1: "disarmed"}


@pytest.mark.parametrize(
    ("table", "names"),
    [
        pytest.param('new Array("  ","Cast 2")', ["Area 1", "Cast 2"], id="blank name"),
        pytest.param('new Array("Cast 1")', ["Cast 1", "Area 2"], id="past the table"),
        pytest.param(
            'new Array(" Cast 1  ","Cast 2")', ["Cast 1", "Cast 2"], id="padded name"
        ),
    ],
)
async def test_area_name_is_trimmed_or_numbered_when_there_is_none(
    hass: HomeAssistant,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    table: str,
    names: list[str],
) -> None:
    panel.set_status(useraccess=[1, 1])
    serve_area_names(panel, table)

    await setup_entry(hass, mock_config_entry)

    entry_id = mock_config_entry.entry_id
    assert original_names(hass, mock_config_entry) == {
        f"{entry_id}_area_0": names[0],
        f"{entry_id}_area_1": names[1],
    }


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize(("service", "letter", "code", "state", "expected"), ACTIONS)
async def test_action_sends_its_command_to_the_area_and_the_state_follows(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    *,
    index: int,
    service: str,
    letter: str,
    code: int,
    state: str,
    expected: str,
) -> None:
    panel.set_status(useraccess=[code, code])
    await setup_entry(hass, mock_config_entry)

    await call(hass, service, AREA_ENTITY_IDS[index])

    assert command_lines(panel) == [f"{STATUS}?area=0{index}&value={letter}"]
    await tick(hass, freezer)
    states = dict.fromkeys(AREA_ENTITY_IDS, state)
    states[AREA_ENTITY_IDS[index]] = expected
    assert alarm_states(hass) == states


async def test_state_is_polled_as_soon_as_the_command_is_accepted(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen_at_setup = len(panel.requests)

    await call(hass, "alarm_arm_away", AREA_0)

    assert panel.request_lines[seen_at_setup:] == [
        f"{STATUS}?area=00&value=r",
        STATUS,
    ]
    assert alarm_states(hass) == {AREA_0: "armed_away"}


@pytest.mark.parametrize(("answer", "times", "cause", "reason"), FAILURES)
async def test_failed_command_raises_command_failed_and_polling_continues(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    *,
    answer: PanelResult,
    times: int,
    cause: type[Exception],
    reason: str,
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen_at_setup = len(panel.requests)
    panel.respond_with("/statuslive.html", answer, times=times, when=is_command)

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "alarm_arm_away", AREA_0)

    assert raised.value.translation_domain == "paradox"
    assert raised.value.translation_key == "command_failed"
    assert raised.value.translation_placeholders == {"area": "Cast 1", "reason": reason}
    assert type(raised.value.__cause__) is cause
    command = f"{STATUS}?area=00&value=r"
    assert panel.request_lines[seen_at_setup:] == [command] * times
    assert alarm_states(hass) == {AREA_0: "disarmed"}

    await tick(hass, freezer)

    assert panel.request_lines[seen_at_setup:] == [command] * times + [STATUS]
    assert alarm_states(hass) == {AREA_0: "disarmed"}


async def test_command_failure_message_names_the_area_and_the_cause(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.respond_with(
        "/statuslive.html",
        FakeResponse(status=500, body="error"),
        times=1,
        when=is_command,
    )

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "alarm_disarm", AREA_0)

    assert str(raised.value) == (
        "The panel did not accept the command for Cast 1: "
        "Request failed with status code 500"
    )


async def test_alarm_reported_by_the_panel_makes_the_area_triggered(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    assert alarm_states(hass) == {AREA_0: "disarmed"}

    panel.set_status(alarms=["0"])
    await tick(hass, freezer)

    assert alarm_states(hass) == {AREA_0: "triggered"}

    panel.set_status(alarms=[])
    await tick(hass, freezer)

    assert alarm_states(hass) == {AREA_0: "disarmed"}


async def test_area_taken_out_of_use_disappears_after_a_reload(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.set_status(useraccess=[1, 1])
    await setup_entry(hass, mock_config_entry)
    assert set(alarm_states(hass)) == {AREA_0, AREA_1}
    panel.set_status(useraccess=[1, 0])

    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert alarm_unique_ids(hass, mock_config_entry) == {
        AREA_0: f"{mock_config_entry.entry_id}_area_0"
    }
    assert alarm_states(hass) == {AREA_0: "disarmed"}
