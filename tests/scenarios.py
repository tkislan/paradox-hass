"""Play the rows of ``tests/spec/ha_scenarios.json``, described in its ``_comment``."""

import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.async_ import get_scheduled_timer_handles
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.paradox.const import DOMAIN
from tests.fake_panel import fault_result
from tests.helpers import entity_id_for, tick
from tests.spec_tables import spec_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from collections.abc import Set as AbstractSet

    from _pytest.mark import ParameterSet
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel

type Row = dict[str, Any]

GROUPS: Final = (
    "startup",
    "keep_alive",
    "armed",
    "sensors",
    "commands",
    "setup_failures",
    "recovery",
)
FILE_KEYS: Final = {"_comment", "login", *GROUPS}
ROW_KEYS: Final = {"name", "ported_from", "note", "panel", "setup", "steps"}
PANEL_KEYS: Final = {
    "zones",
    "statuszone",
    "useraccess",
    "alarms",
    "troubles",
    "accept_login",
    "default_page",
    "down",
    "faults",
}
FAULT_KEYS: Final = {"path", "kind", "times"}
SETUP_KEYS: Final = {"advance_s", "expect"}
STEP_KEYS: Final = {
    "panel_status",
    "panel_zones",
    "panel_fault",
    "panel_expire_session",
    "panel_down",
    "action",
    "wall_clock_step_s",
    "advance_s",
    "expect",
}
STATUS_KEYS: Final = {"statuszone", "useraccess", "alarms", "troubles"}
ACTION_KEYS: Final = {"service", "area"}
EXPECT_KEYS: Final = {
    "entry_state",
    "reauth_in_progress",
    "states",
    "panel_requests",
    "panel_requests_any_order",
    "panel_request_counts",
    "action_error",
}
REQUEST_KEYS: Final = {
    "panel_requests",
    "panel_requests_any_order",
    "panel_request_counts",
}
SETTLE_ROUNDS: Final = 1000
PLATFORMS: Final = {
    "zone": Platform.BINARY_SENSOR,
    "area": Platform.ALARM_CONTROL_PANEL,
    "trouble": Platform.BINARY_SENSOR,
}


def _check_keys(
    where: str,
    value: Mapping[str, Any],
    keys: AbstractSet[str],
    required: AbstractSet[str] = frozenset(),
) -> None:
    if unknown := value.keys() - keys:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    if missing := required - value.keys():
        raise ValueError(f"{where}: missing keys {sorted(missing)}")


def _check_fault(where: str, fault: Mapping[str, Any]) -> None:
    keys = FAULT_KEYS | {"page"} if fault.get("kind") == "page" else FAULT_KEYS
    _check_keys(f"{where}, fault", fault, keys)
    if fault.get("kind") == "hang":
        raise ValueError(f"{where}, fault: kind 'hang' cannot be played")


def _check_expect(where: str, expect: Mapping[str, Any]) -> None:
    _check_keys(f"{where}, expect", expect, EXPECT_KEYS)
    if len(expect.keys() & REQUEST_KEYS) > 1:
        raise ValueError(f"{where}, expect: more than one of {sorted(REQUEST_KEYS)}")


def _check_row(where: str, row: Row) -> None:
    _check_keys(where, row, ROW_KEYS, {"name", "ported_from", "setup"})
    panel = row.get("panel", {})
    _check_keys(f"{where}, panel", panel, PANEL_KEYS)
    for fault in panel.get("faults", []):
        _check_fault(f"{where}, panel", fault)
    _check_keys(f"{where}, setup", row["setup"], SETUP_KEYS, {"expect"})
    _check_expect(f"{where}, setup", row["setup"]["expect"])
    for number, step in enumerate(row.get("steps", []), 1):
        at = f"{where}, step {number}"
        _check_keys(at, step, STEP_KEYS)
        _check_keys(f"{at}, panel_status", step.get("panel_status", {}), STATUS_KEYS)
        if "panel_fault" in step:
            _check_fault(at, step["panel_fault"])
        _check_keys(f"{at}, action", step.get("action", {}), ACTION_KEYS)
        _check_expect(at, step.get("expect", {}))


def scenario_rows(spec: Mapping[str, Any]) -> list[ParameterSet]:
    """
    One ``pytest.param(login, row)`` per row of every group, ``<group>/<name>`` as id.

    Raises ``ValueError`` naming the object, and its row if it is in one, when an
    object of the file has a key its format does not allow, a row or setup lacks one
    it requires, an expect has more than one way to check the requests, or a fault
    has kind ``hang``, which cannot be played.
    """
    _check_keys("ha_scenarios.json", spec, FILE_KEYS)
    params = []
    for group in GROUPS:
        for row in spec[group]:
            row_id = f"{group}/{row.get('name')}"
            _check_row(f"ha_scenarios.json, row {row_id!r}", row)
            params.append(pytest.param(spec["login"], row, id=row_id))
    return params


def _add_fault(panel: FakePanel, fault: Mapping[str, Any]) -> None:
    page = spec_text(fault["page"]) if fault["kind"] == "page" else ""
    panel.respond_with(
        fault["path"],
        fault_result(fault["kind"], panel.session_value, page),
        times=fault.get("times"),
    )


def _build_panel(
    panel: FakePanel, login: Mapping[str, str], spec: Mapping[str, Any]
) -> None:
    panel.session_value = login["session"]
    accepted = {login["session"]: (login["u"], login["p"])}
    panel.credentials = accepted if spec.get("accept_login", True) else {}
    if "zones" in spec:
        panel.zones = [(bitmask, name) for bitmask, name in spec["zones"]]
    panel.set_status(**{key: spec[key] for key in STATUS_KEYS & spec.keys()})
    if "default_page" in spec:
        panel.default_page = spec_text(spec["default_page"])
    panel.down = spec.get("down", False)
    for fault in spec.get("faults", []):
        _add_fault(panel, fault)


def _waits_for_the_clock(task: asyncio.Task[Any]) -> bool:
    # Private attributes: asyncio.capture_call_graph shows that a task is in
    # asyncio.sleep, but not whether a live timer will wake it.
    waiter = task._fut_waiter
    return waiter is not None and any(
        handle._args[:1] == (waiter,)
        for handle in get_scheduled_timer_handles(task.get_loop())
        if not handle.cancelled()
    )


async def _settle(task: asyncio.Task[Any]) -> None:
    for _ in range(SETTLE_ROUNDS):
        if task.done() or _waits_for_the_clock(task):
            return
        await asyncio.sleep(0)
    raise AssertionError("The setup neither ended nor waited for the clock")


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int
) -> None:
    """Move the clock on like ``tick``, not waiting for tasks asleep on the clock."""
    for _ in range(seconds):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        for _ in range(SETTLE_ROUNDS):
            await hass.async_block_till_done()
            # A new login parked between two index page attempts is such a task, and
            # ``tick`` would wait for it forever.
            if all(
                task.done() or _waits_for_the_clock(task)
                for task in hass._background_tasks
            ):
                break
        else:
            raise AssertionError("The tasks neither ended nor waited for the clock")


async def _set_up(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    entry: MockConfigEntry,
    seconds: int | None,
) -> None:
    # Once the integration is set up, the setup of an entry runs in the task that
    # asks for it, which is the task that _waits_for_the_clock looks at.
    assert await async_setup_component(hass, DOMAIN, {})
    entry.add_to_hass(hass)
    if seconds is None:
        await hass.config_entries.async_setup(entry.entry_id)
    else:
        # Not a task of Home Assistant's, which ``tick`` would wait for.
        setup = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
        try:
            # A tick while a request is on its way would fire the request's timeouts.
            for _ in range(seconds):
                await _settle(setup)
                await tick(hass, freezer)
            await _settle(setup)
            assert setup.done(), f"The setup is still running after {seconds} s"
            await setup
        finally:
            setup.cancel()
    await hass.async_block_till_done(wait_background_tasks=True)


async def _act(
    hass: HomeAssistant, entry: MockConfigEntry, action: Mapping[str, Any]
) -> str | None:
    area = f"area_{action['area']}"
    entity_id = entity_id_for(hass, entry, Platform.ALARM_CONTROL_PANEL, area)
    assert entity_id is not None, f"No entity for {area}"
    try:
        await hass.services.async_call(
            "alarm_control_panel",
            action["service"],
            {"entity_id": entity_id},
            blocking=True,
        )
    except HomeAssistantError as err:
        return err.translation_key or repr(err)
    return None


@contextmanager
def _wall_clock() -> Iterator[Callable[[int], None]]:
    """Yield a function that moves ``dt_util.utcnow`` on by the seconds it is given."""
    # Only utcnow is patched: async_fire_time_changed compares timer deadlines with
    # time.time(), which must not move.
    utcnow = dt_util.utcnow
    shift = timedelta()

    def step(seconds: int) -> None:
        nonlocal shift
        shift += timedelta(seconds=seconds)

    with patch.object(dt_util, "utcnow", lambda: utcnow() + shift):
        yield step


async def _play_step(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    entry: MockConfigEntry,
    step: Row,
    *,
    step_wall_clock: Callable[[int], None],
) -> str | None:
    if "panel_status" in step:
        panel.set_status(**step["panel_status"])
    if "panel_zones" in step:
        panel.zones = [(bitmask, name) for bitmask, name in step["panel_zones"]]
    if "panel_fault" in step:
        _add_fault(panel, step["panel_fault"])
    if step.get("panel_expire_session"):
        panel.expire_session()
    if "panel_down" in step:
        panel.down = step["panel_down"]
    error = await _act(hass, entry, step["action"]) if "action" in step else None
    if "wall_clock_step_s" in step:
        step_wall_clock(step["wall_clock_step_s"])
    if "advance_s" in step:
        await _tick(hass, freezer, step["advance_s"])
    return error


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    platform = PLATFORMS[key.partition("_")[0]]
    entity_id = entity_id_for(hass, entry, platform, key)
    if entity_id is None:
        return "absent"
    state = hass.states.get(entity_id)
    return None if state is None else state.state


def _check(
    phase: str,
    hass: HomeAssistant,
    entry: MockConfigEntry,
    expect: Mapping[str, Any],
    *,
    requests: list[str],
    action_error: str | None,
) -> None:
    actual: dict[str, Any] = {"action_error": action_error}
    wanted: dict[str, Any] = {"action_error": expect.get("action_error")}
    if "panel_request_counts" in expect:
        actual["panel_request_counts"] = dict(Counter(requests))
        wanted["panel_request_counts"] = expect["panel_request_counts"]
    elif "panel_requests_any_order" in expect:
        actual["panel_requests"] = sorted(requests)
        wanted["panel_requests"] = sorted(expect["panel_requests_any_order"])
    else:
        actual["panel_requests"] = requests
        wanted["panel_requests"] = expect.get("panel_requests", [])
    if "states" in expect:
        actual["states"] = {key: _state(hass, entry, key) for key in expect["states"]}
        wanted["states"] = expect["states"]
    if "entry_state" in expect:
        actual["entry_state"] = entry.state.value
        wanted["entry_state"] = expect["entry_state"]
    if "reauth_in_progress" in expect:
        actual["reauth_in_progress"] = any(
            entry.async_get_active_flows(hass, {SOURCE_REAUTH})
        )
        wanted["reauth_in_progress"] = expect["reauth_in_progress"]
    assert actual == wanted, f"in {phase}"


async def play(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    entry: MockConfigEntry,
    *,
    login: Mapping[str, str],
    row: Row,
) -> None:
    """
    Set ``entry`` up against ``panel`` and play the steps of ``row``.

    Raises ``AssertionError`` when ``entry`` does not log in with ``login``, and one
    whose message starts ``in setup`` or ``in step <n>`` for the first phase whose
    expectations do not hold.
    """
    assert (entry.data["username"], entry.data["password"]) == (
        login["username"],
        login["password"],
    ), "The config entry does not log in with the scenarios' login"
    _build_panel(panel, login, row.get("panel", {}))

    seen = len(panel.requests)
    setup = row["setup"]
    await _set_up(hass, freezer, entry, setup.get("advance_s"))
    requests = panel.request_lines[seen:]
    _check("setup", hass, entry, setup["expect"], requests=requests, action_error=None)

    with _wall_clock() as step_wall_clock:
        for number, step in enumerate(row.get("steps", []), 1):
            seen = len(panel.requests)
            error = await _play_step(
                hass, freezer, panel, entry, step, step_wall_clock=step_wall_clock
            )
            requests = panel.request_lines[seen:]
            expect = step.get("expect", {})
            _check(
                f"step {number}",
                hass,
                entry,
                expect,
                requests=requests,
                action_error=error,
            )
