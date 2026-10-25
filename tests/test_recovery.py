import asyncio
import logging
from typing import TYPE_CHECKING, Final

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.util.async_ import get_scheduled_timer_handles

from custom_components.paradox.panel.client import INDEX_RETRY_WAIT
from tests.fake_panel import FakeResponse, Hang, Reset, render_index_page
from tests.helpers import advance, hang_once, setup_entry, tick

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel, PanelRequest, PanelResult

PACKAGE_LOGGER: Final = "custom_components.paradox"
STATUS: Final = "GET /statuslive.html"
LOGOUT: Final = "GET /logout.html"
LOGIN: Final = [
    LOGOUT,
    "GET /login_page.html",
    "GET /default.html?u=EBA2095C&p=82016AC9BD9B087D6C393B19C756B255",
    "GET /index.html",
]
RELOGIN_LOG: Final = (
    "Logging in to the panel again after a failed poll: "
    "Request failed with status code 500"
)
HTTP_500: Final = FakeResponse(status=500, body="error")
ZONE_ENTITY_IDS: Final = (
    "binary_sensor.paradox_predsien",
    "binary_sensor.paradox_lava_izba",
    "binary_sensor.paradox_prava_izba",
    "binary_sensor.paradox_spalna",
    "binary_sensor.paradox_obyvacka",
    "binary_sensor.paradox_chodba",
)
SETTLE_ROUNDS: Final = 1000


def zone_states(hass: HomeAssistant) -> dict[str, str]:
    return {
        state.entity_id: state.state for state in hass.states.async_all("binary_sensor")
    }


def zone_unique_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    return {
        registry_entry.entity_id: registry_entry.unique_id
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if registry_entry.domain == Platform.BINARY_SENSOR
    }


def hang_every_request(panel: FakePanel, path: str) -> asyncio.Event:
    arrived = asyncio.Event()

    def hang(_request: PanelRequest) -> PanelResult:
        arrived.set()
        return Hang()

    panel.respond_with(path, hang)
    return arrived


def timeline(panel: FakePanel, since: int, started: float) -> list[tuple[str, int]]:
    """
    List the panel's requests from index ``since`` on.

    Each is ``(path, whole seconds after started)``.
    """
    return [
        (request.path, round(request.received_at - started))
        for request in panel.requests[since:]
    ]


async def wait_for_login_retry(hass: HomeAssistant) -> None:
    """
    Yield until a timer is due one index page retry wait from now.

    A request is a round trip over a real socket, so the login only reaches its sleep
    after a few turns of the event loop. Returns without raising when none comes, and
    the caller's assertions on the requests show what went wrong.
    """
    for _ in range(SETTLE_ROUNDS):
        if any(
            not handle.cancelled()
            and handle.when() == hass.loop.time() + INDEX_RETRY_WAIT
            for handle in get_scheduled_timer_handles(hass.loop)
        ):
            return
        await asyncio.sleep(0)


async def start_login_that_waits(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, panel: FakePanel
) -> int:
    """
    Start a new login that waits for an index page retry, and return the request count.

    The count is that of the panel's requests before the failed poll that starts it.
    """
    panel.respond_with("/index.html", HTTP_500)
    panel.expire_session()
    seen = len(panel.requests)
    await advance(hass, freezer, 1)
    await wait_for_login_retry(hass)
    return seen


async def test_new_login_that_reads_a_renamed_zone_reloads_the_entry(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    zone_1 = er.async_get(hass).async_get("binary_sensor.paradox_lava_izba")
    assert zone_1 is not None
    unique_ids = zone_unique_ids(hass, mock_config_entry)
    seen = len(panel.requests)

    panel.zones[1] = (1, "Kuchyna")
    panel.expire_session()
    await tick(hass, freezer)

    assert panel.request_lines[seen:] == [STATUS, *LOGIN, LOGOUT, *LOGIN, STATUS]
    state = hass.states.get("binary_sensor.paradox_lava_izba")
    assert state is not None
    assert state.attributes["friendly_name"] == "Paradox Kuchyna"
    assert zone_unique_ids(hass, mock_config_entry) == unique_ids
    kept = er.async_get(hass).async_get("binary_sensor.paradox_lava_izba")
    assert kept is not None
    assert kept.id == zone_1.id


async def test_new_login_that_reads_a_renamed_area_reloads_the_entry(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen = len(panel.requests)
    index_page = render_index_page(panel.zones).replace('"Cast 1"', '"Garage"')
    panel.respond_with("/index.html", FakeResponse(body=index_page))
    panel.expire_session()

    await tick(hass, freezer)

    assert panel.request_lines[seen:] == [STATUS, *LOGIN, LOGOUT, *LOGIN, STATUS]


async def test_blocked_answered_failure_ends_a_run_of_unanswered_polls(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.respond_with("/statuslive.html", HTTP_500, times=1)
    await tick(hass, freezer)
    logins = len(panel.requests_to("/login_page.html"))
    # The polls from 2 to 29 s are reset. The poll at 30 s fails with an answer, and
    # the 60 s rule keeps it from logging in, but it ends the run: the 60 s count
    # starts again at 31 s.
    panel.respond_with("/statuslive.html", Reset(), times=2 * 28)
    await tick(hass, freezer, 28)
    panel.respond_with("/statuslive.html", HTTP_500, times=1)
    await tick(hass, freezer)
    panel.respond_with("/statuslive.html", Reset())

    await tick(hass, freezer, 60)

    assert len(panel.requests_to("/login_page.html")) == logins

    await tick(hass, freezer)

    assert len(panel.requests_to("/login_page.html")) == logins + 1


async def unload_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)


async def stop_home_assistant(hass: HomeAssistant, _entry: MockConfigEntry) -> None:
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "close", [unload_entry, stop_home_assistant], ids=["unload", "home assistant stop"]
)
async def test_closing_during_a_new_login_ends_with_the_logout_and_logs_no_problem(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    *,
    close: Callable[[HomeAssistant, MockConfigEntry], Awaitable[None]],
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen = await start_login_that_waits(hass, freezer, panel)
    assert panel.request_lines[seen:] == [STATUS, *LOGIN]

    await close(hass, mock_config_entry)
    await tick(hass, freezer, 10)

    assert panel.request_lines[seen:] == [STATUS, *LOGIN, LOGOUT]
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(PACKAGE_LOGGER) and record.levelno >= logging.WARNING
    ] == []


async def test_failure_59_s_after_a_login_started_sends_no_login_and_60_s_after_one(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=PACKAGE_LOGGER)
    await setup_entry(hass, mock_config_entry)
    # The login that starts at 1 s ends at 2 s, so a failure at 61 s logs in only if
    # the 60 s run from the start of the login, not from its end.
    panel.respond_with("/index.html", HTTP_500, times=1)
    panel.respond_with("/statuslive.html", HTTP_500, times=1)
    await advance(hass, freezer, 1)
    await wait_for_login_retry(hass)
    await advance(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    await tick(hass, freezer, 57)
    assert len(panel.requests_to("/login_page.html")) == 2

    panel.respond_with("/statuslive.html", HTTP_500, times=1)
    await tick(hass, freezer)

    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "unavailable")
    assert len(panel.requests_to("/login_page.html")) == 2

    panel.respond_with("/statuslive.html", HTTP_500, times=1)
    await tick(hass, freezer)

    assert len(panel.requests_to("/login_page.html")) == 3
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "off")
    relogins = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Logging in to the panel again")
    ]
    assert relogins == [RELOGIN_LOG, RELOGIN_LOG]


async def test_new_login_that_fails_counts_for_the_60_s_rule(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen = len(panel.requests)
    panel.respond_with("/statuslive.html", HTTP_500)
    panel.respond_with("/login_page.html", HTTP_500, times=1)

    await tick(hass, freezer)

    assert panel.request_lines[seen:] == [STATUS, LOGOUT, "GET /login_page.html"]
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "unavailable")

    await tick(hass, freezer)

    assert panel.request_lines[seen + 3 :] == [STATUS]


async def test_hanging_poll_blocks_polls_and_keep_alives_until_its_deadline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    started = hass.loop.time()
    seen = len(panel.requests)
    arrived = hang_once(panel, "/statuslive.html")

    await advance(hass, freezer, 1)
    await arrived.wait()
    await advance(hass, freezer, 19)

    assert timeline(panel, seen, started) == [("/statuslive.html", 1)]
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "off")

    await advance(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "unavailable")
    await tick(hass, freezer)

    assert timeline(panel, seen, started) == [
        ("/statuslive.html", 1),
        ("/keep_alive.html", 21),
        ("/statuslive.html", 22),
    ]
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "off")


async def test_hanging_status_page_is_polled_every_21_s_and_logged_in_again_at_84_s(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    started = hass.loop.time()
    seen = len(panel.requests)
    arrived = hang_every_request(panel, "/statuslive.html")

    # Polls go out at 1, 22 and 43 s and fail 20 s later, at 21, 42 and 63 s; the
    # failure at 21 s starts the 60 s count.
    for _ in range(3):
        await advance(hass, freezer, 1)
        await arrived.wait()
        arrived.clear()
        await advance(hass, freezer, 20)
        await hass.async_block_till_done(wait_background_tasks=True)
    await advance(hass, freezer, 1)
    await arrived.wait()
    await advance(hass, freezer, 19)

    # The poll sent at 64 s fails at 84 s, 63 s into the count, and is still hanging
    # at 83 s. Each keep-alive waited behind a poll and went out when that poll
    # failed.
    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "unavailable")
    assert timeline(panel, seen, started) == [
        ("/statuslive.html", 1),
        ("/keep_alive.html", 21),
        ("/statuslive.html", 22),
        ("/keep_alive.html", 42),
        ("/statuslive.html", 43),
        ("/keep_alive.html", 63),
        ("/statuslive.html", 64),
    ]

    await advance(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert timeline(panel, seen, started)[7:] == [
        ("/keep_alive.html", 84),
        ("/logout.html", 84),
        ("/login_page.html", 84),
        ("/default.html", 84),
        ("/index.html", 84),
    ]

    panel.clear_overrides()
    await tick(hass, freezer)

    assert zone_states(hass) == dict.fromkeys(ZONE_ENTITY_IDS, "off")


async def test_unreachable_panel_gets_a_login_attempt_every_61_s(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    started = hass.loop.time()
    seen = len(panel.requests)
    panel.down = True

    await tick(hass, freezer, 122)

    # The first poll at 1 s starts the 60 s count, so the poll at 61 s attempts a
    # login. That attempt ends the run, and the poll at 62 s starts the next count,
    # which ends with the attempt at 122 s. Every request goes out twice, because
    # aiohttp sends a reset GET again.
    assert [
        (path, second)
        for path, second in timeline(panel, seen, started)
        if path not in {"/statuslive.html", "/keep_alive.html"}
    ] == [
        ("/logout.html", 61),
        ("/logout.html", 61),
        ("/login_page.html", 61),
        ("/login_page.html", 61),
        ("/logout.html", 122),
        ("/logout.html", 122),
        ("/login_page.html", 122),
        ("/login_page.html", 122),
    ]


async def test_failed_first_poll_of_the_setup_retries_the_setup_without_a_second_login(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.respond_with("/statuslive.html", HTTP_500)

    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert panel.request_lines == [*LOGIN, STATUS, LOGOUT]
