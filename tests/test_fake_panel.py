import asyncio
import gc
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit

import aiohttp
import pytest
from aiohttp import web

from custom_components.paradox.panel.models import PanelStatus
from custom_components.paradox.panel.pages import (
    is_login_page,
    parse_index_page,
    parse_session_value,
    parse_status_page,
)
from tests.fake_panel import (
    Cut,
    FakePanel,
    FakePanelOptions,
    FakeResponse,
    Hang,
    PanelRequest,
    PanelResult,
    Stall,
    render_index_page,
)
from tests.logs import problem_records

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

SESSION = "0123456789ABCDEF"
LOGIN = "/default.html?u=UU&p=PP"
DATA_PATHS = ["/index.html", "/statuslive.html", "/keep_alive.html"]
UNUSED_SLOT = (0, " ")


def make_panel(**options: Any) -> FakePanel:
    defaults = {"session_value": SESSION, "credentials": {SESSION: ("UU", "PP")}}
    return FakePanel(FakePanelOptions(**(defaults | options)))


def get(panel: FakePanel, target: str) -> PanelResult:
    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    return panel.handle(PanelRequest("GET", target, parts.path, query, {}))


def response_of(result: PanelResult) -> FakeResponse:
    assert isinstance(result, FakeResponse)
    return result


def body_of(result: PanelResult) -> str:
    response = response_of(result)
    assert response.status == 200
    return response.body


def login(panel: FakePanel) -> None:
    get(panel, LOGIN)
    assert panel.logged_in


def served_login_page(body: str) -> bool:
    return is_login_page(body) and parse_session_value(body) == SESSION


async def wait_until(condition: Callable[[], object]) -> None:
    async with asyncio.timeout(1):
        while True:
            if condition():
                return
            await asyncio.sleep(0.005)


def problems(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in problem_records(caplog)]


def test_index_page_drops_zones_past_slot_32() -> None:
    given = [(1, f"Zone {i}") for i in range(33)]

    zones = parse_index_page(render_index_page(given)).zones

    assert zones == tuple(given[:32])


def test_login_page_hands_out_the_session_value() -> None:
    panel = make_panel()

    assert served_login_page(body_of(get(panel, "/login_page.html")))


def test_default_session_value_is_the_captured_one() -> None:
    body = body_of(get(FakePanel(), "/login_page.html"))

    assert parse_session_value(body) == "91AC25D06A0C26BA"


def test_default_page_can_be_replaced() -> None:
    panel = make_panel(default_page="<title>Elsewhere</title>")

    assert body_of(get(panel, LOGIN)) == "<title>Elsewhere</title>"


@pytest.mark.parametrize(
    "target",
    [
        "/default.html?u=UU&p=WRONG",
        "/default.html?u=WRONG&p=PP",
        "/default.html?u=PP&p=UU",
        "/default.html",
    ],
)
def test_other_credentials_get_the_login_page(target: str) -> None:
    panel = make_panel()

    assert served_login_page(body_of(get(panel, target)))
    assert not panel.logged_in


def test_without_configured_credentials_every_login_is_rejected() -> None:
    panel = FakePanel()

    body = body_of(get(panel, "/default.html?u=user&p=1234"))

    assert is_login_page(body)
    assert not panel.logged_in


def test_empty_username_is_accepted_only_when_sent_empty() -> None:
    panel = make_panel(credentials={SESSION: ("", "PP")})

    assert served_login_page(body_of(get(panel, "/default.html?p=PP")))
    assert not panel.logged_in

    assert not is_login_page(body_of(get(panel, "/default.html?u=&p=PP")))
    assert panel.logged_in


def test_credentials_count_only_for_the_current_session_value() -> None:
    panel = make_panel(
        credentials={SESSION: ("UU", "PP"), "OTHER": ("XX", "YY")},
    )
    panel.session_value = "OTHER"

    assert is_login_page(body_of(get(panel, LOGIN)))
    assert not panel.logged_in
    assert not is_login_page(body_of(get(panel, "/default.html?u=XX&p=YY")))
    assert panel.logged_in


def test_a_rejected_login_ends_the_session() -> None:
    panel = make_panel()
    login(panel)

    get(panel, "/default.html?u=UU&p=WRONG")

    assert not panel.logged_in


def test_answer_without_a_session_by_default() -> None:
    panel = make_panel()
    login(panel)
    panel.expire_session()

    assert not is_login_page(body_of(get(panel, "/index.html")))


def test_answer_the_login_page_without_a_session_when_login_is_required() -> None:
    panel = make_panel(require_login=True)

    assert served_login_page(body_of(get(panel, "/index.html")))


def test_answer_data_with_a_session_when_login_is_required() -> None:
    panel = make_panel(require_login=True)
    login(panel)

    assert not is_login_page(body_of(get(panel, "/index.html")))


@pytest.mark.parametrize("path", DATA_PATHS)
def test_answer_the_login_page_again_after_the_session_expires(path: str) -> None:
    panel = make_panel(require_login=True)
    login(panel)
    panel.expire_session()

    assert not panel.logged_in
    assert served_login_page(body_of(get(panel, path)))


def test_status_defaults_to_closed_zones_and_a_disarmed_first_area() -> None:
    status = parse_status_page(body_of(get(make_panel(), "/statuslive.html")))

    assert status == PanelStatus(
        statuszone=(0,) * 32, useraccess=(1, 0), alarms=(), troubles=()
    )


def test_options_set_the_pages() -> None:
    panel = make_panel(
        zones=[(2, "Two")],
        statuszone=[5, *[0] * 31],
        useraccess=[7, 3, 1],
        alarms=["1"],
        troubles=["16", "19"],
    )

    zones = parse_index_page(body_of(get(panel, "/index.html"))).zones
    status = parse_status_page(body_of(get(panel, "/statuslive.html")))

    assert zones == ((2, "Two"), *[UNUSED_SLOT] * 31)
    assert status == PanelStatus(
        statuszone=(5, *[0] * 31),
        useraccess=(7, 3, 1),
        alarms=(1,),
        troubles=(16, 19),
    )


def test_unknown_paths_are_not_found() -> None:
    result = get(make_panel(), "/nope.html")

    assert isinstance(result, FakeResponse)
    assert result.status == 404


def test_panels_built_from_one_options_object_do_not_share_state() -> None:
    options = FakePanelOptions()
    first = FakePanel(options)
    second = FakePanel(options)

    get(first, "/statuslive.html?area=00&value=r")

    status = parse_status_page(body_of(get(second, "/statuslive.html")))
    assert status.useraccess == (1, 0)


@pytest.mark.parametrize(("value", "code"), [("r", 2), ("s", 5), ("p", 4), ("d", 1)])
def test_command_sets_the_area_code_in_the_page_it_is_answered_with(
    value: str, code: int
) -> None:
    panel = make_panel(useraccess=[9, 9])

    status = parse_status_page(
        body_of(get(panel, f"/statuslive.html?area=01&value={value}"))
    )

    assert status.useraccess == (9, code)
    assert panel.useraccess == [9, code]


@pytest.mark.parametrize(
    "query",
    [
        "area=02&value=r",
        "area=-1&value=r",
        "value=r",
        "area=00&value=R",
        "area=00",
    ],
)
def test_command_is_ignored_when_area_or_value_do_not_apply(query: str) -> None:
    panel = make_panel(useraccess=[9, 9])

    status = parse_status_page(body_of(get(panel, f"/statuslive.html?{query}")))

    assert status.useraccess == (9, 9)
    assert panel.useraccess == [9, 9]


def test_command_needs_a_session_when_login_is_required() -> None:
    panel = make_panel(require_login=True, useraccess=[9, 9])

    body = body_of(get(panel, "/statuslive.html?area=00&value=r"))

    assert served_login_page(body)
    assert panel.useraccess == [9, 9]


def test_command_state_stays_for_later_status_pages() -> None:
    panel = make_panel()
    get(panel, "/statuslive.html?area=00&value=s")

    status = parse_status_page(body_of(get(panel, "/statuslive.html")))

    assert status.useraccess == (5, 0)


def test_alarms_and_troubles_are_written_as_quoted_strings() -> None:
    panel = make_panel()
    panel.set_status(alarms=["0", "2"], troubles=["16", "19", "38"])

    body = body_of(get(panel, "/statuslive.html"))

    assert 'tbl_alarmes = new Array("0","2")' in body
    assert 'tbl_troubles = new Array("16","19","38")' in body
    status = parse_status_page(body)
    assert (status.alarms, status.troubles) == ((0, 2), (16, 19, 38))


def test_only_the_given_tables_are_replaced() -> None:
    panel = make_panel(alarms=["1"])

    panel.set_status(statuszone=[6, *[0] * 31])

    status = parse_status_page(body_of(get(panel, "/statuslive.html")))
    assert status == PanelStatus(
        statuszone=(6, *[0] * 31), useraccess=(1, 0), alarms=(1,), troubles=()
    )


def test_an_empty_list_clears_a_table() -> None:
    panel = make_panel(alarms=["1"], troubles=["16"])

    panel.set_status(alarms=[], troubles=[])

    status = parse_status_page(body_of(get(panel, "/statuslive.html")))
    assert (status.alarms, status.troubles) == ((), ())


def test_useraccess_is_replaced() -> None:
    panel = make_panel()

    panel.set_status(useraccess=[7, 2])

    status = parse_status_page(body_of(get(panel, "/statuslive.html")))
    assert status.useraccess == (7, 2)


def test_a_function_is_called_with_the_request_and_counts_as_one_use() -> None:
    panel = make_panel()
    seen: list[PanelRequest] = []

    def responder(request: PanelRequest) -> PanelResult:
        seen.append(request)
        return FakeResponse(body=request.query["area"])

    panel.respond_with("/statuslive.html", responder, times=1)

    first = get(panel, "/statuslive.html?area=07")
    second = get(panel, "/statuslive.html")

    assert body_of(first) == "07"
    assert [request.query for request in seen] == [{"area": "07"}]
    assert parse_status_page(body_of(second)).statuszone == (0,) * 32


def test_clear_overrides_restores_normal_answers() -> None:
    panel = make_panel()
    panel.respond_with("/index.html", FakeResponse(status=500))
    panel.respond_with("/keep_alive.html", FakeResponse(status=500), times=3)

    panel.clear_overrides()

    assert not is_login_page(body_of(get(panel, "/index.html")))
    assert body_of(get(panel, "/keep_alive.html")) == ""


@pytest.fixture
async def http() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


def url(panel: FakePanel, target: str) -> str:
    return f"http://{panel.host}{target}"


async def fetch(
    http: aiohttp.ClientSession, panel: FakePanel, target: str, **kwargs: Any
) -> tuple[int, str]:
    async with http.get(url(panel, target), **kwargs) as response:
        return response.status, await response.text()


async def test_requests_are_recorded_as_received(
    fake_panel: FakePanel, http: aiohttp.ClientSession
) -> None:
    loop = asyncio.get_running_loop()
    before = loop.time()

    await fetch(
        http,
        fake_panel,
        "/default.html?u=A%2BB&p=C%20D",
        headers={"X-Probe": "Value"},
    )

    after = loop.time()
    [request] = fake_panel.requests
    assert request.method == "GET"
    assert request.url == "/default.html?u=A%2BB&p=C%20D"
    assert request.path == "/default.html"
    assert request.query == {"u": "A+B", "p": "C D"}
    assert request.headers["x-probe"] == "Value"
    assert "X-Probe" not in request.headers
    assert request.headers["host"] == fake_panel.host
    assert before <= request.received_at <= after


async def test_every_method_and_path_reaches_the_panel(
    fake_panel: FakePanel, http: aiohttp.ClientSession
) -> None:
    async with http.post(url(fake_panel, "/a/b.html?c=1")) as response:
        assert response.status == 404

    assert fake_panel.request_lines == ["POST /a/b.html?c=1"]


async def test_a_redirect_without_a_body_is_passed_on(
    fake_panel: FakePanel, http: aiohttp.ClientSession
) -> None:
    fake_panel.respond_with(
        "/index.html", FakeResponse(status=302, headers={"Location": "/moved.html"})
    )

    async with http.get(
        url(fake_panel, "/index.html"), allow_redirects=False
    ) as response:
        assert response.status == 302
        assert response.headers["Location"] == "/moved.html"
        assert await response.read() == b""


async def test_pages_are_html_without_a_charset(
    fake_panel: FakePanel, http: aiohttp.ClientSession
) -> None:
    async with http.get(url(fake_panel, "/login_page.html")) as response:
        assert response.headers["Content-Type"] == "text/html"


async def test_stall_sends_the_prefix_and_then_nothing(
    fake_panel: FakePanel, http: aiohttp.ClientSession
) -> None:
    fake_panel.respond_with("/statuslive.html", Stall("<html>part"))

    async with http.get(url(fake_panel, "/statuslive.html")) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/html"
        assert await response.content.readany() == b"<html>part"
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await response.content.read()
        response.close()


@pytest.mark.parametrize("status", [200, 500])
async def test_cut_ends_the_body_early_by_closing_the_connection(
    fake_panel: FakePanel,
    http: aiohttp.ClientSession,
    caplog: pytest.LogCaptureFixture,
    status: int,
) -> None:
    fake_panel.respond_with("/statuslive.html", Cut("<html>part", status=status))

    async with http.get(url(fake_panel, "/statuslive.html")) as response:
        assert response.status == status
        assert response.headers["Content-Type"] == "text/html"
        assert response.headers["Content-Length"] == "1000"
        with pytest.raises(aiohttp.ClientPayloadError):
            async with asyncio.timeout(1):
                await response.read()

    assert problems(caplog) == []


async def test_stall_close_is_noticed_when_handlers_are_not_cancelled(
    http: aiohttp.ClientSession, socket_enabled: None
) -> None:
    panel = FakePanel()
    panel.respond_with("/statuslive.html", Stall("<html>part"))
    runner = web.AppRunner(panel.app())
    await runner.setup()
    try:
        assert runner.server is not None
        assert not runner.server.handler_cancellation
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        panel.host = f"127.0.0.1:{runner.addresses[0][1]}"
        response = await http.get(url(panel, "/statuslive.html"))
        await response.content.readany()

        response.close()

        await wait_until(lambda: panel.stalls_closed_by_client == 1)
    finally:
        panel.release()
        await runner.cleanup()


async def test_release_frees_hangs_and_stalls_quietly(
    fake_panel: FakePanel,
    http: aiohttp.ClientSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_panel.respond_with("/index.html", Hang())
    fake_panel.respond_with("/statuslive.html", Stall("<html>part"))
    hung = asyncio.create_task(fetch(http, fake_panel, "/index.html"))
    stalled = await http.get(url(fake_panel, "/statuslive.html"))
    await stalled.content.readany()
    await wait_until(lambda: "GET /index.html" in fake_panel.request_lines)

    fake_panel.release()

    async with asyncio.timeout(1):
        with pytest.raises(aiohttp.ServerDisconnectedError):
            await hung
        with pytest.raises(aiohttp.ClientPayloadError):
            await stalled.content.read()
    stalled.close()
    gc.collect()
    assert fake_panel.stalls_closed_by_client == 0
    assert problems(caplog) == []
