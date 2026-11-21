import asyncio
import logging
from typing import TYPE_CHECKING, Final

import pytest
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.paradox.const import DOMAIN
from custom_components.paradox.coordinator import CommandNotConfirmedError
from custom_components.paradox.panel.errors import (
    ParadoxAuthError,
    ParadoxClosedError,
    ParadoxConnectionError,
    ParadoxHttpError,
    ParadoxSessionExpiredError,
)
from custom_components.paradox.panel.models import PanelCommand
from tests.fake_panel import (
    FakeResponse,
    Hang,
    PanelRequest,
    PanelResult,
    Reset,
    render_index_page,
    render_login_page,
)
from tests.helpers import (
    advance,
    run_while_ticking,
    settle,
    setup_entry,
    tick,
    tick_during,
)
from tests.logs import problem_records

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel

PACKAGE_LOGGER: Final = "custom_components.paradox"
STATUS: Final = "GET /statuslive.html"
ARM_REQUEST: Final = f"{STATUS}?area=00&value=r"
DISARM_REQUEST: Final = f"{STATUS}?area=00&value=d"
AREA_0: Final = "alarm_control_panel.paradox_cast_1"
AREA_1: Final = "alarm_control_panel.paradox_cast_2"
AREA_ENTITY_IDS: Final = (AREA_0, AREA_1)
INDEX_AREA_NAMES: Final = 'new Array("Cast 1","Cast 2")'
HTTP_500: Final = FakeResponse(status=500, body="error")
NOT_CONFIRMED: Final = "the area did not reach the new state within 20 s"
CONFIRMING_CODES: Final = {
    "alarm_arm_away": {2, 7},
    "alarm_arm_home": {5, 7, 10},
    "alarm_arm_night": {4, 7},
    "alarm_disarm": {1, 8, 9},
}

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


def info_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == PACKAGE_LOGGER and record.levelno == logging.INFO
    ]


def hang_and_signal(arrived: asyncio.Event) -> PanelResult:
    arrived.set()
    return Hang()


def report_area_code(panel: FakePanel, code: int) -> None:
    """Answer every command with an empty page, after setting area 1 to ``code``."""

    def respond(_request: PanelRequest) -> PanelResult:
        panel.set_status(useraccess=[code, 0])
        return FakeResponse(body="")

    panel.respond_with("/statuslive.html", respond, when=is_command)


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


async def test_disarm_right_after_a_failed_poll_reaches_the_panel(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    panel.set_status(useraccess=[2, 0])
    await setup_entry(hass, mock_config_entry)
    panel.down = True
    await tick(hass, freezer)
    panel.down = False

    await call(hass, "alarm_disarm", AREA_0)

    assert command_lines(panel) == [f"{STATUS}?area=00&value=d"]
    assert alarm_states(hass) == {AREA_0: "disarmed"}


@pytest.mark.parametrize(("answer", "times", "cause", "reason"), FAILURES)
async def test_command_failing_on_every_attempt_raises_command_failed_at_20_s(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    *,
    answer: PanelResult,
    times: int,
    cause: type[Exception],
    reason: str,
) -> None:
    await setup_entry(hass, mock_config_entry)
    caplog.clear()
    panel.respond_with("/statuslive.html", answer, when=is_command)

    task = asyncio.create_task(call(hass, "alarm_arm_away", AREA_0))
    try:
        await tick_during(hass, freezer, task, 19)
        assert not task.done()
        await tick_during(hass, freezer, task, 1)
        assert task.done()
        with pytest.raises(HomeAssistantError) as raised:
            await task
    finally:
        task.cancel()

    assert raised.value.translation_domain == "paradox"
    assert raised.value.translation_key == "command_failed"
    assert raised.value.translation_placeholders == {"area": "Cast 1", "reason": reason}
    assert type(raised.value.__cause__) is cause
    # Attempts at 0, 5, 10 and 15 s.
    assert command_lines(panel) == [ARM_REQUEST] * (4 * times)
    assert alarm_states(hass) == {AREA_0: "disarmed"}
    assert [
        (record.name, record.levelno, record.getMessage())
        for record in problem_records(caplog)
    ] == [
        *(
            (
                PACKAGE_LOGGER,
                logging.WARNING,
                f"Command arm for area 1, attempt {attempt}: failed: {reason}",
            )
            for attempt in (1, 2, 3, 4)
        ),
        (
            PACKAGE_LOGGER,
            logging.WARNING,
            "Command arm for area 1: not confirmed within 20 s after 4 attempts",
        ),
    ]


async def test_command_failure_message_names_the_area_and_the_cause(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    panel.set_status(useraccess=[2, 0])
    await setup_entry(hass, mock_config_entry)
    panel.respond_with("/statuslive.html", HTTP_500, when=is_command)

    with pytest.raises(HomeAssistantError) as raised:
        await run_while_ticking(hass, freezer, call(hass, "alarm_disarm", AREA_0), 20)

    assert str(raised.value) == (
        "The panel did not accept the command for Cast 1: "
        "Request failed with status code 500"
    )


async def test_command_failing_once_is_sent_again_5_s_later_and_confirmed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, mock_config_entry)
    caplog.set_level(logging.INFO, logger=PACKAGE_LOGGER)
    caplog.clear()
    panel.respond_with("/statuslive.html", Reset(), times=2, when=is_command)

    task = asyncio.create_task(call(hass, "alarm_arm_away", AREA_0))
    try:
        await tick_during(hass, freezer, task, 4)
        assert not task.done()
        await tick_during(hass, freezer, task, 1)
        assert task.done()
        await task
    finally:
        task.cancel()

    assert command_lines(panel) == [ARM_REQUEST] * 3
    assert alarm_states(hass) == {AREA_0: "armed_away"}
    assert info_lines(caplog) == [
        "Command arm for area 1, attempt 2: sent",
        "Command arm for area 1: confirmed after attempt 2",
    ]


async def test_command_the_panel_answers_but_ignores_raises_at_20_s(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.respond_with(
        "/statuslive.html", FakeResponse(body="<html></html>"), when=is_command
    )

    with pytest.raises(HomeAssistantError) as raised:
        await run_while_ticking(hass, freezer, call(hass, "alarm_arm_away", AREA_0), 20)

    assert raised.value.translation_key == "command_failed"
    assert raised.value.translation_placeholders == {
        "area": "Cast 1",
        "reason": NOT_CONFIRMED,
    }
    assert type(raised.value.__cause__) is CommandNotConfirmedError


async def test_command_applied_by_the_panel_whose_answer_is_lost_is_not_sent_again(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)

    def apply_and_reset(_request: PanelRequest) -> PanelResult:
        panel.set_status(useraccess=[2, 0])
        return Reset()

    panel.respond_with("/statuslive.html", apply_and_reset, times=2, when=is_command)

    await run_while_ticking(hass, freezer, call(hass, "alarm_arm_away", AREA_0), 0)

    # One attempt, which aiohttp sent again after the reset.
    assert command_lines(panel) == [ARM_REQUEST] * 2
    assert alarm_states(hass) == {AREA_0: "armed_away"}


async def test_arm_is_confirmed_by_exit_delay(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    report_area_code(panel, 7)

    await run_while_ticking(hass, freezer, call(hass, "alarm_arm_away", AREA_0), 0)

    assert command_lines(panel) == [ARM_REQUEST]
    assert alarm_states(hass) == {AREA_0: "arming"}


async def test_disarm_is_confirmed_while_the_area_is_still_in_alarm(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    panel.set_status(useraccess=[2, 0], alarms=["0"])
    await setup_entry(hass, mock_config_entry)

    await run_while_ticking(hass, freezer, call(hass, "alarm_disarm", AREA_0), 0)

    assert command_lines(panel) == [DISARM_REQUEST]
    assert alarm_states(hass) == {AREA_0: "triggered"}


@pytest.mark.parametrize(
    ("service", "code"),
    [
        (service, code)
        for service, codes in CONFIRMING_CODES.items()
        for code in sorted(codes)
    ],
)
async def test_command_is_confirmed_by_each_of_its_area_codes(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    *,
    service: str,
    code: int,
) -> None:
    await setup_entry(hass, mock_config_entry)
    report_area_code(panel, code)

    await run_while_ticking(hass, freezer, call(hass, service, AREA_0), 0)

    assert len(command_lines(panel)) == 1


@pytest.mark.parametrize(
    ("service", "code"),
    [
        (service, code)
        for service, codes in CONFIRMING_CODES.items()
        for code in range(1, 11)
        if code not in codes
    ],
)
async def test_command_is_not_confirmed_by_any_other_area_code(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    *,
    service: str,
    code: int,
) -> None:
    await setup_entry(hass, mock_config_entry)
    report_area_code(panel, code)

    task = asyncio.create_task(call(hass, service, AREA_0))
    try:
        await settle(hass, task)

        assert not task.done()
    finally:
        task.cancel()


async def test_command_confirmed_by_a_later_timed_poll_is_not_sent_again(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, mock_config_entry)
    caplog.set_level(logging.INFO, logger=PACKAGE_LOGGER)
    caplog.clear()
    panel.respond_with(
        "/statuslive.html", FakeResponse(body=""), times=1, when=is_command
    )

    task = asyncio.create_task(call(hass, "alarm_arm_away", AREA_0))
    try:
        await tick_during(hass, freezer, task, 1)
        assert not task.done()
        assert command_lines(panel) == [ARM_REQUEST]
        panel.set_status(useraccess=[7, 0])
        # The poll at 2 s shows exit delay, and the check at 3 s sees it.
        await tick_during(hass, freezer, task, 2)
        assert task.done()
        await task
    finally:
        task.cancel()

    assert command_lines(panel) == [ARM_REQUEST]
    assert alarm_states(hass) == {AREA_0: "arming"}
    assert info_lines(caplog) == [
        "Command arm for area 1, attempt 1: sent",
        "Command arm for area 1: confirmed after attempt 1",
    ]


async def test_status_kept_after_a_failed_poll_does_not_confirm_a_command(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, mock_config_entry)
    caplog.set_level(logging.INFO, logger=PACKAGE_LOGGER)
    caplog.clear()
    seen_at_setup = len(panel.requests)
    panel.down = True

    task = asyncio.create_task(call(hass, "alarm_disarm", AREA_0))
    try:
        await settle(hass, task)
        # The read after the command failed, and kept the disarmed status.
        assert (
            panel.request_lines[seen_at_setup:] == [DISARM_REQUEST] * 2 + [STATUS] * 2
        )
        assert alarm_states(hass) == {AREA_0: "disarmed"}
        assert not task.done()
        panel.down = False
        await tick_during(hass, freezer, task, 3)
        assert task.done()
        await task
    finally:
        task.cancel()

    assert command_lines(panel) == [DISARM_REQUEST] * 2
    assert alarm_states(hass) == {AREA_0: "disarmed"}
    assert info_lines(caplog) == [
        "Command disarm for area 1: confirmed after attempt 1"
    ]


async def test_command_retrying_when_the_entry_unloads_ends_with_command_failed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.respond_with("/statuslive.html", HTTP_500, when=is_command)

    task = asyncio.create_task(call(hass, "alarm_arm_away", AREA_0))
    try:
        await tick_during(hass, freezer, task, 2)
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        # The next attempt is due 5 s into the action.
        await tick_during(hass, freezer, task, 3)
        assert task.done()
        with pytest.raises(HomeAssistantError) as raised:
            await task
    finally:
        task.cancel()

    assert raised.value.translation_key == "command_failed"
    assert type(raised.value.__cause__) is ParadoxClosedError
    assert command_lines(panel) == [ARM_REQUEST]
    await tick(hass, freezer, 5)
    assert panel.request_lines[-1] == "GET /logout.html"


async def test_command_meeting_a_rejected_login_fails_at_once_with_the_area_unavailable(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.credentials = {}
    panel.expire_session()

    with pytest.raises(HomeAssistantError) as raised:
        await run_while_ticking(hass, freezer, call(hass, "alarm_arm_away", AREA_0), 0)

    assert raised.value.translation_key == "command_failed"
    assert raised.value.translation_placeholders == {
        "area": "Cast 1",
        "reason": "the panel rejected the login",
    }
    assert type(raised.value.__cause__) is ParadoxAuthError
    assert command_lines(panel) == [ARM_REQUEST]
    assert mock_config_entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert {
        state.state
        for state in hass.states.async_all(("alarm_control_panel", "binary_sensor"))
    } == {"unavailable"}
    warnings = [
        record.getMessage()
        for record in problem_records(caplog)
        if record.name == PACKAGE_LOGGER and record.levelno == logging.WARNING
    ]
    assert warnings == [
        (
            "Command arm for area 1, attempt 1: failed: "
            "Command answered with the login page"
        ),
        (
            "Command arm for area 1: stopped after attempt 1: "
            "the panel rejected the login"
        ),
    ]


async def test_command_request_hanging_at_the_deadline_ends_the_command_at_20_s(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    arrived = asyncio.Event()
    panel.respond_with("/statuslive.html", HTTP_500, times=1, when=is_command)
    panel.respond_with(
        "/statuslive.html",
        lambda _request: hang_and_signal(arrived),
        times=1,
        when=is_command,
    )

    # Called directly: advance waits for the task in which Home Assistant runs a
    # service call, and that task would wait on the hanging request.
    task = asyncio.create_task(
        mock_config_entry.runtime_data.async_send_command(0, PanelCommand.ARM)
    )
    try:
        await tick_during(hass, freezer, task, 4)
        await advance(hass, freezer, 1)
        await arrived.wait()
        await advance(hass, freezer, 14)
        assert not task.done()
        await advance(hass, freezer, 1)
        assert task.done()
        with pytest.raises(CommandNotConfirmedError, match=rf"^{NOT_CONFIRMED}$"):
            await task
    finally:
        task.cancel()

    assert command_lines(panel) == [ARM_REQUEST] * 2


async def test_status_read_hanging_at_20_s_ends_a_command_and_keeps_entities_available(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    arrived = asyncio.Event()
    commands = 0

    def answer(_request: PanelRequest) -> PanelResult:
        nonlocal commands
        commands += 1
        if commands == 2:
            panel.respond_with(
                "/statuslive.html",
                lambda _request: hang_and_signal(arrived),
                times=1,
                when=lambda request: not is_command(request),
            )
        return FakeResponse(body="<html></html>")

    panel.respond_with("/statuslive.html", answer, times=2, when=is_command)

    coordinator = mock_config_entry.runtime_data
    # Called directly, as in the test of a hanging command request.
    task = asyncio.create_task(coordinator.async_send_command(0, PanelCommand.ARM))
    try:
        await tick_during(hass, freezer, task, 4)
        await advance(hass, freezer, 1)
        await arrived.wait()
        await advance(hass, freezer, 14)
        assert not task.done()
        await advance(hass, freezer, 1)
        assert task.done()
        with pytest.raises(CommandNotConfirmedError, match=rf"^{NOT_CONFIRMED}$"):
            await task
    finally:
        task.cancel()

    assert command_lines(panel) == [ARM_REQUEST] * 2
    assert coordinator.last_update_success


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
