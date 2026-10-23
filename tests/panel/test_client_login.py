import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

import pytest

from custom_components.paradox.panel.client import ParadoxClient
from custom_components.paradox.panel.errors import (
    ParadoxConnectionError,
    ParadoxError,
    ParadoxPageError,
)
from custom_components.paradox.panel.models import PanelLayout
from tests.fake_panel import FakeResponse, Hang, Reset, render_index_page
from tests.logs import problem_records
from tests.panel.expected_errors import assert_error
from tests.spec_tables import spec_rows, spec_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from _pytest.mark import ParameterSet

    from tests.fake_panel import FakePanel, PanelResult

CLIENT_LOGGER: Final = "custom_components.paradox.panel.client"
FLOW_PATHS: Final = ("/logout.html", "/login_page.html", "/default.html", "/index.html")
ZONE_SLOTS: Final = 32
UNUSED_SLOT: Final = (0, " ")

PANEL_ROW_KEYS: Final = {"panel", "expected"}
HEADER_ROW_KEYS: Final = {
    "path",
    "headers",
    "complete",
    "headers_starting_with",
    "absent",
}
RULE_KEYS: Final = {
    "file",
    "body",
    "zones",
    "zones_by_slot",
    "destroy",
    "hang",
    "status",
    "headers",
    "times",
}

# fast_timing runs the client at 1/20 of production, where the spec times are given.
FAST_TIMING_SCALE: Final = 1 / 20
# Timers may fire a little early.
EARLY_TOLERANCE: Final = 0.010

# aiohttp resends a GET once when the connection is dropped before any response
# arrived, so a request answered with a Reset reaches the panel twice per client call.
# The spec rows were written for a client that sends it once and stay untouched, so
# the interpreter accounts for the resend.
SENDS_PER_RESET_CALL: Final = 2

GOLDEN_U: Final = "EBA2095C"
GOLDEN_P: Final = "82016AC9BD9B087D6C393B19C756B255"
PLAYGROUND_SESSION: Final = "A86572A01074210A"
PLAYGROUND_U: Final = "87E973C2"
PLAYGROUND_P: Final = "2FB788E0C53B0F6668F9BF79698B8B6D"

SAMPLE_ZONES: Final = (
    (1, "Predsien"),
    (1, "Lava izba"),
    (1, "Prava izba"),
    (1, "Spalna"),
    (1, "Obyvacka"),
    (1, "Chodba"),
    *[UNUSED_SLOT] * 26,
)


def rules_of(responses: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return responses if isinstance(responses, list) else [responses]


def check_rule(path: str, rule: dict[str, Any], *, allow_destroy: bool = True) -> None:
    if unknown := rule.keys() - RULE_KEYS:
        raise ValueError(f"{path}: the rule keys {sorted(unknown)} are not played")
    if not rule.get("destroy"):
        return
    if others := rule.keys() - {"destroy", "times"}:
        raise ValueError(f"{path}: destroy next to {sorted(others)} is not played")
    if not allow_destroy:
        raise ValueError(f"{path}: destroy is not played in this group of rows")


def page_body(rule: dict[str, Any]) -> str:
    if "file" in rule:
        return spec_text({"file": rule["file"]})
    if "body" in rule:
        return str(rule["body"])
    if "zones" in rule:
        return render_index_page([(area, name) for area, name in rule["zones"]])
    if "zones_by_slot" in rule:
        slots = [UNUSED_SLOT] * ZONE_SLOTS
        for slot, (area, name) in rule["zones_by_slot"].items():
            slots[int(slot)] = (area, name)
        return render_index_page(slots)
    return ""


def to_response(rule: dict[str, Any]) -> PanelResult:
    if rule.get("destroy"):
        return Reset()
    if rule.get("hang"):
        return Hang()
    return FakeResponse(
        status=rule.get("status", 200),
        headers={"Content-Type": "text/html", **rule.get("headers", {})},
        body=page_body(rule),
    )


def apply_panel(
    fake_panel: FakePanel, panel: dict[str, Any], *, allow_destroy: bool = True
) -> None:
    for path, responses in panel.items():
        for rule in rules_of(responses):
            check_rule(path, rule, allow_destroy=allow_destroy)
            times = rule.get("times")
            if rule.get("destroy") and times is not None:
                times *= SENDS_PER_RESET_CALL
            fake_panel.respond_with(path, to_response(rule), times)


def destroyed_calls(rules: list[dict[str, Any]], calls: int) -> list[bool]:
    destroyed: list[bool] = []
    for rule in rules:
        left = calls - len(destroyed)
        uses = left if rule.get("times") is None else min(rule["times"], left)
        destroyed += [bool(rule.get("destroy"))] * uses
    return destroyed + [False] * (calls - len(destroyed))


def path_rules(row: dict[str, Any], path: str) -> list[dict[str, Any]]:
    return rules_of(row["panel"][path]) if path in row["panel"] else []


def expected_counts(row: dict[str, Any]) -> dict[str, int]:
    calls = row["expected"].get("request_counts")
    if calls is None:
        calls = dict.fromkeys(FLOW_PATHS, 1)
    counts = {}
    for path in {*FLOW_PATHS, *calls}:
        path_calls = calls.get(path, 0)
        resets = sum(destroyed_calls(path_rules(row, path), path_calls))
        counts[path] = path_calls + resets * (SENDS_PER_RESET_CALL - 1)
    return counts


def expected_index_times_ms(row: dict[str, Any]) -> list[int]:
    spec_ms: list[int] = row["expected"]["index_at_ms"]
    destroyed = destroyed_calls(path_rules(row, "/index.html"), len(spec_ms))
    return [
        at_ms
        for at_ms, was_destroyed in zip(spec_ms, destroyed, strict=True)
        for _ in range(SENDS_PER_RESET_CALL if was_destroyed else 1)
    ]


async def outcome_of[T](call: Awaitable[T]) -> T | ParadoxError:
    try:
        return await call
    except ParadoxError as error:
        return error


def assert_failed(outcome: object, message: str) -> None:
    assert isinstance(outcome, ParadoxError), f"expected {message!r}, got {outcome!r}"
    assert_error(outcome, message)


def assert_login_outcome(expected: dict[str, Any], outcome: object) -> None:
    if "error" in expected:
        assert_failed(outcome, expected["error"])
        return
    assert isinstance(outcome, PanelLayout), f"expected a layout, got {outcome!r}"
    assert [list(zone) for zone in outcome.zones] == expected["zoneTuples"]


def logged_problems(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [
        (record.name, record.levelno, record.getMessage())
        for record in problem_records(caplog)
    ]


def assert_warnings(caplog: pytest.LogCaptureFixture, expected: dict[str, Any]) -> None:
    assert logged_problems(caplog) == [
        (CLIENT_LOGGER, logging.WARNING, message)
        for message in expected.get("warnings", [])
    ]


def assert_requests(fake_panel: FakePanel, row: dict[str, Any]) -> None:
    counts = expected_counts(row)
    assert {path: len(fake_panel.requests_to(path)) for path in counts} == counts
    assert len(fake_panel.requests) == sum(counts.values())
    if (query := row["expected"].get("default_query")) is not None:
        assert (
            fake_panel.requests_to("/default.html")[0].url == f"/default.html?{query}"
        )


def assert_not_early(
    arrivals: Sequence[float], spec_ms: Sequence[int], origin: float
) -> None:
    assert len(arrivals) == len(spec_ms)
    for position, (arrived, at_ms) in enumerate(zip(arrivals, spec_ms, strict=True)):
        earliest = at_ms / 1000 * FAST_TIMING_SCALE - EARLY_TOLERANCE
        assert arrived - origin >= earliest, (
            f"event {position} came after {arrived - origin:.3f} s, "
            f"not {earliest:.3f} s"
        )


def header_rows(*, complete: bool) -> list[ParameterSet]:
    rows = spec_rows("login_cases", "request_headers", HEADER_ROW_KEYS)
    return [
        row
        for row in rows
        if isinstance(spec := row.values[0], dict)
        and bool(spec.get("complete")) is complete
    ]


@pytest.mark.parametrize(
    ("rule", "allow_destroy"),
    [
        ({"bogus": True}, True),
        ({"destroy": True, "status": 500}, True),
        ({"destroy": True, "hang": True}, True),
        ({"destroy": True}, False),
    ],
)
def test_rule_shapes_that_are_not_played_are_rejected(
    rule: dict[str, Any], *, allow_destroy: bool
) -> None:
    with pytest.raises(ValueError, match="not played"):
        check_rule("/index.html", rule, allow_destroy=allow_destroy)


@pytest.mark.parametrize("row", spec_rows("login_cases", "login", PANEL_ROW_KEYS))
async def test_login_row(
    row: dict[str, Any],
    fake_panel: FakePanel,
    client: ParadoxClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    apply_panel(fake_panel, row["panel"])

    outcome = await outcome_of(client.async_login())

    assert_login_outcome(row["expected"], outcome)
    assert_warnings(caplog, row["expected"])
    assert_requests(fake_panel, row)


@pytest.mark.usefixtures("fast_timing")
@pytest.mark.parametrize("row", spec_rows("login_cases", "index_retry", PANEL_ROW_KEYS))
async def test_index_retry_row(
    row: dict[str, Any],
    fake_panel: FakePanel,
    client: ParadoxClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    apply_panel(fake_panel, row["panel"])

    outcome = await outcome_of(client.async_login())
    settled_at = asyncio.get_running_loop().time()

    assert_login_outcome(row["expected"], outcome)
    assert_warnings(caplog, row["expected"])
    assert_requests(fake_panel, row)
    # Unlike after a hang, each wait here starts after the answer to the request before
    # it, so no arrival can come early measured from the first one, however late the
    # panel handler ran.
    index_arrivals = [r.received_at for r in fake_panel.requests_to("/index.html")]
    spec_ms = expected_index_times_ms(row)
    assert_not_early(
        [*index_arrivals, settled_at],
        [*spec_ms, spec_ms[-1]],
        origin=index_arrivals[0],
    )


@pytest.mark.usefixtures("fast_timing")
@pytest.mark.parametrize("row", spec_rows("login_cases", "hangs", PANEL_ROW_KEYS))
async def test_hangs_row(
    row: dict[str, Any],
    fake_panel: FakePanel,
    client: ParadoxClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The times of a destroyed request would need the resend, and no row has one.
    apply_panel(fake_panel, row["panel"], allow_destroy=False)
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    outcome = await outcome_of(client.async_login())
    settled_at = loop.time()

    expected = row["expected"]
    assert_login_outcome(expected, outcome)
    assert_warnings(caplog, expected)
    assert [r.path for r in fake_panel.requests] == [p for _, p in expected["requests"]]
    assert_not_early(
        [*(r.received_at for r in fake_panel.requests), settled_at],
        [*(at_ms for at_ms, _ in expected["requests"]), expected["settles_at_ms"]],
        origin=started_at,
    )


@pytest.mark.parametrize("row", spec_rows("login_cases", "logout", PANEL_ROW_KEYS))
async def test_logout_row(
    row: dict[str, Any], fake_panel: FakePanel, client: ParadoxClient
) -> None:
    apply_panel(fake_panel, row["panel"])

    outcome = await outcome_of(client.async_logout())

    if "error" in row["expected"]:
        assert_failed(outcome, row["expected"]["error"])
    else:
        assert outcome is None


@pytest.mark.parametrize("row", header_rows(complete=True))
async def test_login_request_headers(
    row: dict[str, Any], fake_panel: FakePanel, client: ParadoxClient
) -> None:
    await client.async_login()

    headers = fake_panel.requests_to(row["path"])[0].headers
    assert headers == {
        name.lower(): value.replace("{hostname}", fake_panel.host)
        for name, value in row["headers"].items()
    }


# The row describes the bridge's HTTP client (axios): its ``headers`` and
# ``headers_starting_with`` are not portable, and aiohttp always sends
# Accept-Encoding. Only the headers that must not be sent are checked.
@pytest.mark.parametrize(
    "send",
    [
        pytest.param(ParadoxClient.async_login, id="first request of a login"),
        pytest.param(ParadoxClient.async_close, id="sent at close"),
    ],
)
@pytest.mark.parametrize("row", header_rows(complete=False))
async def test_logout_request_headers(
    row: dict[str, Any],
    send: Callable[[ParadoxClient], Awaitable[object]],
    fake_panel: FakePanel,
    client: ParadoxClient,
) -> None:
    await send(client)

    assert fake_panel.requests[0].path == "/logout.html"
    headers = fake_panel.requests_to(row["path"])[0].headers
    for name in row["absent"]:
        if name != "Accept-Encoding":
            assert name.lower() not in headers


async def test_login_returns_the_layout_of_the_index_page(
    client: ParadoxClient,
) -> None:
    layout = await client.async_login()

    assert layout.zones == SAMPLE_ZONES
    assert layout.zone_slots == (0, 1, 2, 3, 4, 5)
    assert layout.area_names == ("Cast 1", "Cast 2")
    assert len(layout.trouble_names) == 45
    assert layout.trouble_names[0].startswith("Vyčistiť dymový snímač, potiaž")
    assert layout.trouble_names[0].endswith("alebo zlyhanie komunikácie slučky")
    assert layout.trouble_names[-2:] == ("Tamper krytu narušený", "")


async def test_login_replaces_bytes_the_declared_charset_cannot_decode(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with(
        "/index.html",
        FakeResponse(
            headers={"Content-Type": "text/html; charset=ascii"},
            body=render_index_page([(1, "Ob\u00fdvacka")]),
        ),
    )

    layout = await client.async_login()

    assert layout.zones[0] == (1, "Ob\ufffd\ufffdvacka")


async def test_second_login_repeats_the_sequence_with_the_session_value_just_fetched(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.credentials[PLAYGROUND_SESSION] = (PLAYGROUND_U, PLAYGROUND_P)

    await client.async_login()
    fake_panel.session_value = PLAYGROUND_SESSION
    await client.async_login()

    assert fake_panel.request_lines == [
        "GET /logout.html",
        "GET /login_page.html",
        f"GET /default.html?u={GOLDEN_U}&p={GOLDEN_P}",
        "GET /index.html",
        "GET /logout.html",
        "GET /login_page.html",
        f"GET /default.html?u={PLAYGROUND_U}&p={PLAYGROUND_P}",
        "GET /index.html",
    ]
    assert fake_panel.logged_in


async def test_login_with_a_session_value_too_long_for_the_key_fails_after_two_requests(
    fake_panel: FakePanel, client: ParadoxClient, caplog: pytest.LogCaptureFixture
) -> None:
    fake_panel.session_value = "A" * 300

    with pytest.raises(ParadoxPageError) as caught:
        await client.async_login()

    assert fake_panel.request_lines == ["GET /logout.html", "GET /login_page.html"]
    assert logged_problems(caplog) == []
    # The ValueError of the key derivation would carry the credentials in its frames.
    assert caught.value.__context__ is None


async def test_a_request_answered_with_a_reset_reaches_the_panel_twice(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    # The Reset accounting above depends on aiohttp's resend; this fails first if
    # aiohttp stops it.
    fake_panel.respond_with("/logout.html", Reset())

    with pytest.raises(ParadoxConnectionError):
        await client.async_logout()

    assert fake_panel.request_lines == ["GET /logout.html", "GET /logout.html"]
