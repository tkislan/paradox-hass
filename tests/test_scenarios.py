import copy
import re
from typing import TYPE_CHECKING, Any, Final

import pytest
from homeassistant.util import dt as dt_util

from tests.fake_panel import FakeResponse
from tests.scenarios import play, scenario_rows
from tests.spec_tables import load_spec, spec_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel, PanelRequest, PanelResult
    from tests.scenarios import Row

SCENARIOS: Final = scenario_rows(load_spec("ha_scenarios"))
LOGIN: Final = load_spec("ha_scenarios")["login"]
STATUS: Final = "GET /statuslive.html"
KEEP_ALIVE: Final = "GET /keep_alive.html?msgid=1"
ARM: Final = "GET /statuslive.html?area=00&value=r"
SETUP_REQUESTS: Final = [
    "GET /logout.html",
    "GET /login_page.html",
    "GET /default.html?u=EBA2095C&p=82016AC9BD9B087D6C393B19C756B255",
    "GET /index.html",
    STATUS,
]
ROW_WITH_EVERY_CHECK: Final = {
    "name": "checked",
    "ported_from": "startup/checked",
    "setup": {
        "expect": {
            "entry_state": "loaded",
            "reauth_in_progress": False,
            "panel_requests": SETUP_REQUESTS,
            "states": {"area_0": "disarmed", "zone_0": "off", "zone_6": "absent"},
        }
    },
    "steps": [
        {
            "action": {"service": "alarm_arm_away", "area": 0},
            "expect": {
                "panel_requests": [ARM, STATUS],
                "states": {"area_0": "armed_away"},
            },
        },
        {"panel_status": {"statuszone": [1]}, "expect": {}},
        {
            "advance_s": 3,
            "expect": {
                "panel_requests_any_order": [STATUS, KEEP_ALIVE, STATUS, STATUS],
                "states": {"zone_0": "on"},
            },
        },
        {
            "panel_fault": {"path": "/statuslive.html", "kind": "http_500", "times": 1},
            "action": {"service": "alarm_disarm", "area": 0},
            "expect": {
                "action_error": "command_failed",
                "panel_requests": [ARM[:-1] + "d"],
            },
        },
        {
            "advance_s": 3,
            "expect": {"panel_request_counts": {STATUS: 3, KEEP_ALIVE: 1}},
        },
    ],
}

VALID_ROW: Final = {
    "name": "row",
    "ported_from": "startup/row",
    "panel": {"faults": [{"path": "/index.html", "kind": "reset", "times": 2}]},
    "setup": {"expect": {}},
    "steps": [
        {
            "panel_status": {"useraccess": [1, 0]},
            "panel_fault": {"path": "/keep_alive.html", "kind": "page", "page": ""},
            "action": {"service": "alarm_disarm", "area": 0},
            "wall_clock_step_s": -120,
            "expect": {},
        }
    ],
}
ROW_AT: Final = "ha_scenarios.json, row 'startup/row'"


def valid_spec() -> dict[str, Any]:
    groups = (
        "keep_alive",
        "armed",
        "sensors",
        "commands",
        "setup_failures",
        "recovery",
    )
    return {
        "_comment": "",
        "login": {},
        "startup": [copy.deepcopy(VALID_ROW)],
        **{group: [] for group in groups},
    }


def row_of(spec: dict[str, Any]) -> Row:
    row: Row = spec["startup"][0]
    return row


@pytest.mark.parametrize(("login", "row"), SCENARIOS)
async def test_scenario(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    *,
    login: dict[str, str],
    row: Row,
) -> None:
    await play(hass, freezer, panel, mock_config_entry, login=login, row=row)


def test_rows_of_every_group_are_loaded_with_group_and_name_as_id() -> None:
    groups = (
        "startup",
        "keep_alive",
        "armed",
        "sensors",
        "commands",
        "setup_failures",
        "recovery",
    )
    spec = valid_spec() | {group: [copy.deepcopy(VALID_ROW)] for group in groups}

    ids = [param.id for param in scenario_rows(spec)]

    assert ids == [
        "startup/row",
        "keep_alive/row",
        "armed/row",
        "sensors/row",
        "commands/row",
        "setup_failures/row",
        "recovery/row",
    ]


@pytest.mark.parametrize(
    ("part", "where"),
    [
        pytest.param(lambda spec: spec, "ha_scenarios.json", id="file"),
        pytest.param(row_of, ROW_AT, id="row"),
        pytest.param(
            lambda spec: row_of(spec)["panel"], f"{ROW_AT}, panel", id="panel"
        ),
        pytest.param(
            lambda spec: row_of(spec)["panel"]["faults"][0],
            f"{ROW_AT}, panel, fault",
            id="fault",
        ),
        pytest.param(
            lambda spec: row_of(spec)["setup"], f"{ROW_AT}, setup", id="setup"
        ),
        pytest.param(
            lambda spec: row_of(spec)["setup"]["expect"],
            f"{ROW_AT}, setup, expect",
            id="setup expect",
        ),
        pytest.param(
            lambda spec: row_of(spec)["steps"][0], f"{ROW_AT}, step 1", id="step"
        ),
        pytest.param(
            lambda spec: row_of(spec)["steps"][0]["panel_status"],
            f"{ROW_AT}, step 1, panel_status",
            id="panel status",
        ),
        pytest.param(
            lambda spec: row_of(spec)["steps"][0]["panel_fault"],
            f"{ROW_AT}, step 1, fault",
            id="step fault",
        ),
        pytest.param(
            lambda spec: row_of(spec)["steps"][0]["action"],
            f"{ROW_AT}, step 1, action",
            id="action",
        ),
        pytest.param(
            lambda spec: row_of(spec)["steps"][0]["expect"],
            f"{ROW_AT}, step 1, expect",
            id="step expect",
        ),
    ],
)
def test_loading_rejects_an_unknown_key(
    part: Callable[[dict[str, Any]], dict[str, Any]], where: str
) -> None:
    spec = valid_spec()
    part(spec)["unknown"] = 1

    message = f"{where}: unknown keys ['unknown']"
    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        scenario_rows(spec)


@pytest.mark.parametrize(
    ("part", "key", "where"),
    [
        pytest.param(
            row_of, "name", "ha_scenarios.json, row 'startup/None'", id="name"
        ),
        pytest.param(row_of, "ported_from", ROW_AT, id="ported_from"),
        pytest.param(row_of, "setup", ROW_AT, id="setup"),
        pytest.param(
            lambda spec: row_of(spec)["setup"],
            "expect",
            f"{ROW_AT}, setup",
            id="setup expect",
        ),
    ],
)
def test_loading_rejects_a_missing_key(
    part: Callable[[dict[str, Any]], dict[str, Any]], key: str, where: str
) -> None:
    spec = valid_spec()
    del part(spec)[key]

    message = f"{where}: missing keys [{key!r}]"
    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        scenario_rows(spec)


def test_loading_rejects_a_page_on_a_fault_of_another_kind() -> None:
    spec = valid_spec()
    row_of(spec)["panel"]["faults"][0]["page"] = "<html></html>"

    with pytest.raises(ValueError, match=r"panel, fault: unknown keys \['page'\]"):
        scenario_rows(spec)


@pytest.mark.parametrize(
    ("part", "where"),
    [
        pytest.param(
            lambda spec: row_of(spec)["panel"]["faults"][0],
            f"{ROW_AT}, panel, fault",
            id="panel",
        ),
        pytest.param(
            lambda spec: row_of(spec)["steps"][0]["panel_fault"],
            f"{ROW_AT}, step 1, fault",
            id="step",
        ),
    ],
)
def test_loading_rejects_a_hang_fault(
    part: Callable[[dict[str, Any]], dict[str, Any]], where: str
) -> None:
    spec = valid_spec()
    fault = part(spec)
    fault.pop("page", None)
    fault["kind"] = "hang"

    message = f"{where}: kind 'hang' cannot be played"
    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        scenario_rows(spec)


async def test_a_row_that_holds_passes(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    row = copy.deepcopy(ROW_WITH_EVERY_CHECK)

    await play(hass, freezer, panel, mock_config_entry, login=LOGIN, row=row)


async def test_a_panel_down_step_resets_every_request_until_the_panel_is_up(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    row = {
        "setup": {"expect": {"panel_requests": SETUP_REQUESTS}},
        "steps": [
            {
                "panel_down": True,
                "advance_s": 1,
                "expect": {
                    "panel_requests": [STATUS, STATUS],
                    "states": {"area_0": "unavailable"},
                },
            },
            {
                "panel_down": False,
                "advance_s": 1,
                "expect": {
                    "panel_requests": [STATUS],
                    "states": {"area_0": "disarmed"},
                },
            },
        ],
    }

    await play(hass, freezer, panel, mock_config_entry, login=LOGIN, row=row)


def seconds_ahead_at_polls(panel: FakePanel) -> list[float]:
    """
    Return a list that fills with the seconds ``dt_util.utcnow`` is ahead per request.

    A request is any one for ``/statuslive.html``, the arm command's included.
    """
    unshifted = dt_util.utcnow
    seen: list[float] = []

    def respond(_request: PanelRequest) -> PanelResult:
        seen.append((dt_util.utcnow() - unshifted()).total_seconds())
        return FakeResponse(body=spec_text({"file": "data/unarmed.html"}))

    panel.respond_with("/statuslive.html", respond)
    return seen


async def test_wall_clock_steps_shift_utcnow_and_add_up_over_steps(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    original = dt_util.utcnow
    seen = seconds_ahead_at_polls(panel)
    row = {
        "setup": {"expect": {"panel_requests": SETUP_REQUESTS}},
        "steps": [
            {
                "wall_clock_step_s": -120,
                "advance_s": 1,
                "expect": {"panel_requests": [STATUS]},
            },
            {
                "action": {"service": "alarm_arm_away", "area": 0},
                "wall_clock_step_s": 3600,
                "advance_s": 1,
                "expect": {"panel_requests": [ARM, STATUS, STATUS]},
            },
            {
                "advance_s": 1,
                "expect": {"panel_requests_any_order": [STATUS, KEEP_ALIVE]},
            },
        ],
    }

    await play(hass, freezer, panel, mock_config_entry, login=LOGIN, row=row)

    # Setup, step 1; the action's request and its poll, then step 2's tick; step 3.
    assert seen == [0, -120, -120, -120, 3480, 3480]
    assert dt_util.utcnow is original


async def test_the_wall_clock_is_back_when_a_step_check_fails(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    original = dt_util.utcnow
    seen = seconds_ahead_at_polls(panel)
    row = {
        "setup": {"expect": {"panel_requests": SETUP_REQUESTS}},
        "steps": [
            {
                "wall_clock_step_s": 3600,
                "advance_s": 1,
                "expect": {"panel_requests": []},
            }
        ],
    }

    with pytest.raises(AssertionError, match=r"^in step 1\n"):
        await play(hass, freezer, panel, mock_config_entry, login=LOGIN, row=row)

    assert seen == [0, 3600]
    assert dt_util.utcnow is original


async def test_a_default_page_answers_accepted_credentials(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    row = {
        "panel": {"default_page": "<html><head><title>Other</title></head></html>"},
        "setup": {
            "expect": {
                "entry_state": "setup_error",
                "panel_requests": [*SETUP_REQUESTS[:3], "GET /logout.html"],
            }
        },
    }

    await play(hass, freezer, panel, mock_config_entry, login=LOGIN, row=row)


async def test_a_page_fault_answers_with_its_page(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    # armed.html serves useraccess (7, 0), the default panel (1, 0).
    fault = {
        "path": "/statuslive.html",
        "kind": "page",
        "page": {"file": "data/armed.html"},
    }
    row = {
        "panel": {"faults": [fault]},
        "setup": {
            "expect": {"panel_requests": SETUP_REQUESTS, "states": {"area_0": "arming"}}
        },
    }

    await play(hass, freezer, panel, mock_config_entry, login=LOGIN, row=row)


@pytest.mark.parametrize(
    ("change", "phase"),
    [
        pytest.param(
            lambda row: row["setup"]["expect"].update(entry_state="setup_retry"),
            "setup",
            id="entry",
        ),
        pytest.param(
            lambda row: row["setup"]["expect"].update(reauth_in_progress=True),
            "setup",
            id="reauth",
        ),
        pytest.param(
            lambda row: row["setup"]["expect"].update(states={"area_0": "armed_away"}),
            "setup",
            id="state",
        ),
        pytest.param(
            lambda row: row["setup"]["expect"].update(states={"zone_6": "off"}),
            "setup",
            id="absent",
        ),
        pytest.param(
            lambda row: row["setup"]["expect"].update(states={"zone_0": "absent"}),
            "setup",
            id="exists",
        ),
        pytest.param(
            lambda row: row["setup"]["expect"].update(
                panel_requests=SETUP_REQUESTS[:-1]
            ),
            "setup",
            id="requests",
        ),
        pytest.param(
            lambda row: row["steps"][0]["expect"].update(panel_requests=[STATUS, ARM]),
            "step 1",
            id="request order",
        ),
        pytest.param(
            lambda row: row["steps"][0]["expect"].pop("panel_requests"),
            "step 1",
            id="no requests",
        ),
        pytest.param(
            lambda row: row["steps"][0]["expect"].update(action_error="command_failed"),
            "step 1",
            id="action error",
        ),
        pytest.param(
            lambda row: row["steps"][2]["expect"].update(
                panel_requests_any_order=[STATUS, STATUS, STATUS]
            ),
            "step 3",
            id="any order",
        ),
        pytest.param(
            lambda row: row["steps"][3]["expect"].pop("action_error"),
            "step 4",
            id="no action error",
        ),
        pytest.param(
            lambda row: row["steps"][4]["expect"].update(
                panel_request_counts={STATUS: 3}
            ),
            "step 5",
            id="counts",
        ),
    ],
)
async def test_a_row_with_a_wrong_expectation_fails_in_its_phase(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    *,
    change: Callable[[Row], object],
    phase: str,
) -> None:
    row = copy.deepcopy(ROW_WITH_EVERY_CHECK)
    change(row)

    with pytest.raises(AssertionError, match=rf"^in {phase}\n"):
        await play(hass, freezer, panel, mock_config_entry, login=LOGIN, row=row)


@pytest.mark.parametrize(
    ("part", "where"),
    [
        pytest.param(
            lambda spec: row_of(spec)["setup"]["expect"], f"{ROW_AT}, setup", id="setup"
        ),
        pytest.param(
            lambda spec: row_of(spec)["steps"][0]["expect"],
            f"{ROW_AT}, step 1",
            id="step",
        ),
    ],
)
def test_loading_rejects_an_expect_with_two_kinds_of_request_check(
    part: Callable[[dict[str, Any]], dict[str, Any]], where: str
) -> None:
    spec = valid_spec()
    part(spec).update(panel_requests=[], panel_request_counts={})

    kinds = "['panel_request_counts', 'panel_requests', 'panel_requests_any_order']"
    message = f"{where}, expect: more than one of {kinds}"
    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        scenario_rows(spec)
