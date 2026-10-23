import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import aiohttp
import pytest

from custom_components.paradox.panel import client as client_module
from custom_components.paradox.panel.client import ParadoxClient
from custom_components.paradox.panel.errors import (
    ParadoxClosedError,
    ParadoxConnectionError,
    ParadoxError,
    ParadoxHttpError,
    ParadoxPageError,
    ParadoxTimeoutError,
)
from custom_components.paradox.panel.models import PanelCommand, PanelStatus
from tests.fake_panel import (
    Cut,
    FakePanel,
    FakeResponse,
    Hang,
    PanelRequest,
    PanelResult,
    Reset,
    Stall,
)
from tests.logs import problem_records
from tests.panel.expected_errors import assert_error
from tests.spec_tables import spec_rows, spec_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

type Call = Callable[[ParadoxClient], Awaitable[object]]
type Arrange = Callable[[FakePanel], str]

LOGGER_NAME: Final = "custom_components.paradox.panel.client"
HASHED_U: Final = "EBA2095C"
HASHED_P: Final = "82016AC9BD9B087D6C393B19C756B255"
# asyncio timers fire at most a clock tick early.
TIMER_SLACK: Final = 0.01
STALL: Final = Stall("<html>part")


@dataclass(frozen=True)
class Operation:
    path: str
    call: Call


OPERATIONS: Final = {
    "getStatus": Operation("/statuslive.html", ParadoxClient.async_get_status),
    "sendKeepAlive": Operation("/keep_alive.html", ParadoxClient.async_keep_alive),
    "arm": Operation(
        "/statuslive.html", lambda client: client.async_command(0, PanelCommand.ARM)
    ),
    "disarm": Operation(
        "/statuslive.html", lambda client: client.async_command(0, PanelCommand.DISARM)
    ),
}

CALLS_AFTER_CLOSE: Final[dict[str, Call]] = {
    "login": ParadoxClient.async_login,
    "logout": ParadoxClient.async_logout,
    "status": ParadoxClient.async_get_status,
    "keep-alive": ParadoxClient.async_keep_alive,
    "command": lambda client: client.async_command(0, PanelCommand.ARM),
}


async def wait_until(condition: Callable[[], object]) -> None:
    async with asyncio.timeout(1):
        while True:
            if condition():
                return
            await asyncio.sleep(0.005)


def problems(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in problem_records(caplog)]


def paths(panel: FakePanel) -> list[str]:
    return [request.path for request in panel.requests]


def serving(result: PanelResult) -> Arrange:
    def arrange(panel: FakePanel) -> str:
        panel.respond_with("/statuslive.html", result)
        return panel.host

    return arrange


def refusing(panel: FakePanel) -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return f"127.0.0.1:{port}"


async def failure_of_status(panel: FakePanel, arrange: Arrange) -> ParadoxError:
    async with aiohttp.ClientSession() as session:
        panel_client = ParadoxClient(session, arrange(panel), "user", "1234")
        with pytest.raises(ParadoxError) as caught:
            await panel_client.async_get_status()
    return caught.value


def exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    pending = [error]
    while pending:
        link = pending.pop()
        if any(link is seen for seen in chain):
            continue
        chain.append(link)
        pending.extend(
            linked
            for linked in (link.__cause__, link.__context__)
            if linked is not None
        )
    return chain


def assert_no_credentials(error: BaseException) -> None:
    chain = exception_chain(error)
    assert error in chain
    for link in chain:
        for text in (str(link), repr(link)):
            assert HASHED_U not in text, type(link).__name__
            assert HASHED_P not in text, type(link).__name__


@pytest.mark.parametrize(
    "row",
    spec_rows("status_pages", "requests", {"operation", "request", "absent_headers"}),
)
async def test_operation_sends_the_spec_request(
    fake_panel: FakePanel, client: ParadoxClient, row: dict[str, Any]
) -> None:
    await OPERATIONS[row["operation"]].call(client)

    assert fake_panel.request_lines == [row["request"]]
    for name in row["absent_headers"]:
        assert name.lower() not in fake_panel.requests[0].headers


async def test_command_sends_none_of_the_browser_headers(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    await client.async_command(0, PanelCommand.ARM)

    headers = fake_panel.requests[0].headers
    for name in ("referer", "cookie", "authorization", "accept-language"):
        assert name not in headers


@pytest.mark.parametrize("operation", ["arm", "disarm", "sendKeepAlive"])
@pytest.mark.parametrize(
    "row",
    spec_rows("status_pages", "body_ignoring_responses", {"response", "expected"}),
)
async def test_response_body_is_ignored(
    fake_panel: FakePanel,
    client: ParadoxClient,
    row: dict[str, Any],
    operation: str,
) -> None:
    target = OPERATIONS[operation]
    response = row["response"]
    fake_panel.respond_with(
        target.path,
        FakeResponse(status=response["status"], body=spec_text(response["body"])),
    )
    expected = row["expected"]

    if "error" in expected:
        with pytest.raises(ParadoxError) as caught:
            await target.call(client)
        assert_error(caught.value, expected["error"])
    else:
        assert expected == {"result": None}
        assert await target.call(client) is None

    assert len(fake_panel.requests) == 1


@pytest.mark.parametrize(
    "row",
    spec_rows(
        "status_pages", "login_page_responses", {"operation", "response", "expected"}
    ),
)
async def test_login_page_answer_is_a_failure(
    fake_panel: FakePanel, client: ParadoxClient, row: dict[str, Any]
) -> None:
    target = OPERATIONS[row["operation"]]
    response = row["response"]
    fake_panel.respond_with(
        target.path,
        FakeResponse(status=response["status"], body=spec_text(response["body"])),
    )

    with pytest.raises(ParadoxError) as caught:
        await target.call(client)

    assert_error(caught.value, row["expected"]["error"])
    assert len(fake_panel.requests) == 1


@pytest.mark.parametrize(
    ("area", "command", "request_line"),
    [
        pytest.param(
            0, PanelCommand.ARM, "GET /statuslive.html?area=00&value=r", id="arm"
        ),
        pytest.param(
            0, PanelCommand.SLEEP, "GET /statuslive.html?area=00&value=p", id="sleep"
        ),
        pytest.param(
            1,
            PanelCommand.STAY,
            "GET /statuslive.html?area=01&value=s",
            id="area is zero padded",
        ),
        pytest.param(
            10,
            PanelCommand.DISARM,
            "GET /statuslive.html?area=10&value=d",
            id="two digit area",
        ),
    ],
)
async def test_command_request_line(
    fake_panel: FakePanel,
    client: ParadoxClient,
    area: int,
    command: PanelCommand,
    request_line: str,
) -> None:
    await client.async_command(area, command)

    assert fake_panel.request_lines == [request_line]


async def test_status_tables_are_returned_as_tuples(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.set_status(
        statuszone=[1, 0, 5, *[0] * 29], alarms=["1"], troubles=["0", "3"]
    )

    assert await client.async_get_status() == PanelStatus(
        statuszone=(1, 0, 5, *[0] * 29), useraccess=(1, 0), alarms=(1,), troubles=(0, 3)
    )


@pytest.mark.usefixtures("fast_timing")
async def test_stalled_status_times_out_and_the_client_closes_the_connection(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/statuslive.html", STALL)
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(ParadoxTimeoutError) as caught:
        await client.async_get_status()

    deadline = client_module.REQUEST_TIMEOUT
    assert deadline - TIMER_SLACK <= loop.time() - started < deadline + 1
    assert str(caught.value) == "timeout of 20000ms exceeded"
    await wait_until(lambda: fake_panel.stalls_closed_by_client == 1)
    assert fake_panel.request_lines == ["GET /statuslive.html"]


@pytest.mark.usefixtures("fast_timing")
async def test_connection_that_never_opens_fails_after_the_connect_timeout(
    unopened_host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Far from the connect timeout, so that a stalled event loop cannot make the
    # deadline win.
    monkeypatch.setattr(client_module, "REQUEST_TIMEOUT", 5.0)
    attempts: list[object] = []

    async def count_attempt(*args: object) -> None:
        attempts.append(args)

    trace = aiohttp.TraceConfig()
    trace.on_connection_create_start.append(count_attempt)
    loop = asyncio.get_running_loop()
    started = loop.time()

    async with aiohttp.ClientSession(trace_configs=[trace]) as session:
        panel_client = ParadoxClient(session, unopened_host, "user", "1234")
        with pytest.raises(ParadoxConnectionError) as caught:
            await panel_client.async_get_status()

    # Exactly this class: the 20 s deadline would give its ParadoxTimeoutError subclass.
    assert type(caught.value) is ParadoxConnectionError
    assert str(caught.value) == "connect timeout of 100ms exceeded"
    assert len(attempts) == 1
    assert loop.time() - started >= client_module.CONNECT_TIMEOUT - TIMER_SLACK


def test_production_timing_is_the_bridges_and_the_timeout_message_names_it() -> None:
    # No fast_timing: it replaces these constants, so no other test sees the production
    # values.
    assert client_module.REQUEST_TIMEOUT == 20.0
    assert client_module.CONNECT_TIMEOUT == 2.0
    assert client_module.INDEX_RETRY_WAIT == 1.0
    assert str(ParadoxTimeoutError()) == (
        f"timeout of {client_module.REQUEST_TIMEOUT * 1000:.0f}ms exceeded"
    )


@pytest.mark.usefixtures("fast_timing", "socket_enabled")
@pytest.mark.parametrize(
    ("arrange", "error"),
    [
        pytest.param(serving(Reset()), ParadoxConnectionError, id="reset connection"),
        pytest.param(refusing, ParadoxConnectionError, id="refused connection"),
        pytest.param(serving(Hang()), ParadoxTimeoutError, id="no answer"),
        pytest.param(serving(STALL), ParadoxTimeoutError, id="stalled body"),
        pytest.param(
            serving(
                FakeResponse(
                    status=500,
                    headers={"Content-Type": "text/html", "Content-Length": "1000"},
                    body="boom",
                )
            ),
            ParadoxTimeoutError,
            id="HTTP 500 with a body that never ends",
        ),
        pytest.param(
            serving(Cut("<html>part")), ParadoxConnectionError, id="body cut short"
        ),
        pytest.param(
            serving(Cut("boom", status=500)),
            ParadoxConnectionError,
            id="HTTP 500 with a body cut short",
        ),
        pytest.param(
            serving(
                FakeResponse(
                    status=302,
                    headers={
                        "Content-Type": "text/html",
                        "Location": "/statuslive.html",
                    },
                )
            ),
            ParadoxConnectionError,
            id="redirect loop",
        ),
    ],
)
async def test_unanswered_status_request_is_a_connection_error(
    fake_panel: FakePanel, arrange: Arrange, error: type[ParadoxError]
) -> None:
    caught = await failure_of_status(fake_panel, arrange)

    assert type(caught) is error
    assert isinstance(caught, ParadoxConnectionError)


@pytest.mark.parametrize(
    ("arrange", "error", "status"),
    [
        pytest.param(
            serving(FakeResponse(status=500, body="boom")),
            ParadoxHttpError,
            500,
            id="HTTP 500",
        ),
        pytest.param(
            serving(FakeResponse(status=404, body="Not found")),
            ParadoxHttpError,
            404,
            id="HTTP 404",
        ),
        pytest.param(
            serving(FakeResponse(body="<html></html>")),
            ParadoxPageError,
            None,
            id="page without status tables",
        ),
        pytest.param(
            serving(FakeResponse(body=spec_text({"file": "data/login_page.html"}))),
            ParadoxPageError,
            None,
            id="login page",
        ),
    ],
)
async def test_answered_status_failure_is_not_a_connection_error(
    fake_panel: FakePanel,
    arrange: Arrange,
    error: type[ParadoxError],
    status: int | None,
) -> None:
    caught = await failure_of_status(fake_panel, arrange)

    assert type(caught) is error
    assert not isinstance(caught, ParadoxConnectionError)
    assert getattr(caught, "status", None) == status


async def test_error_status_is_an_http_error_when_the_session_raises_for_status(
    fake_panel: FakePanel,
) -> None:
    fake_panel.respond_with("/statuslive.html", FakeResponse(status=503, body="busy"))

    async with aiohttp.ClientSession(raise_for_status=True) as session:
        panel_client = ParadoxClient(session, fake_panel.host, "user", "1234")
        with pytest.raises(ParadoxHttpError) as caught:
            await panel_client.async_get_status()

    assert caught.value.status == 503


async def test_status_299_is_a_success(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/keep_alive.html", FakeResponse(status=299))

    assert await client.async_keep_alive() is None


async def test_status_300_is_an_http_error(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/keep_alive.html", FakeResponse(status=300))

    with pytest.raises(ParadoxHttpError) as caught:
        await client.async_keep_alive()

    assert caught.value.status == 300


# hex fails to decode with a LookupError, undefined with a UnicodeError.
@pytest.mark.parametrize("charset", ["hex", "undefined"])
async def test_status_page_declaring_a_charset_that_is_no_text_codec_is_parsed(
    fake_panel: FakePanel, client: ParadoxClient, charset: str
) -> None:
    fake_panel.respond_with(
        "/statuslive.html",
        FakeResponse(
            headers={"Content-Type": f"text/html; charset={charset}"},
            body=spec_text({"file": "data/unarmed.html"}),
        ),
    )

    assert await client.async_get_status() == PanelStatus(
        statuszone=(5, 0, 0, 0, 0, 6, *[0] * 26),
        useraccess=(1, 0),
        alarms=(),
        troubles=(16, 19, 38),
    )


async def test_close_logs_out_last(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    await client.async_login()
    await client.async_get_status()

    await client.async_close()

    assert paths(fake_panel) == [
        "/logout.html",
        "/login_page.html",
        "/default.html",
        "/index.html",
        "/statuslive.html",
        "/logout.html",
    ]
    assert not fake_panel.logged_in


@pytest.mark.parametrize("call", CALLS_AFTER_CLOSE.values(), ids=CALLS_AFTER_CLOSE)
async def test_call_after_close_raises_without_a_request_or_a_warning(
    fake_panel: FakePanel,
    client: ParadoxClient,
    caplog: pytest.LogCaptureFixture,
    call: Call,
) -> None:
    await client.async_close()

    with pytest.raises(ParadoxClosedError):
        await call(client)

    assert fake_panel.request_lines == ["GET /logout.html"]
    assert problems(caplog) == []


@pytest.mark.usefixtures("fast_timing")
async def test_close_ends_a_login_in_progress(
    fake_panel: FakePanel, client: ParadoxClient, caplog: pytest.LogCaptureFixture
) -> None:
    fake_panel.respond_with("/index.html", Hang())
    login = asyncio.create_task(client.async_login())
    await wait_until(lambda: fake_panel.requests_to("/index.html"))

    await client.async_close()

    with pytest.raises(ParadoxClosedError):
        await login
    assert paths(fake_panel) == [
        "/logout.html",
        "/login_page.html",
        "/default.html",
        "/index.html",
        "/logout.html",
    ]
    assert not fake_panel.logged_in
    assert problems(caplog) == []


@pytest.mark.usefixtures("fast_timing")
async def test_close_during_the_last_index_attempt_raises_closed_error(
    fake_panel: FakePanel, client: ParadoxClient, caplog: pytest.LogCaptureFixture
) -> None:
    closes: list[asyncio.Task[None]] = []

    def fail_index_page(_request: PanelRequest) -> FakeResponse:
        if len(fake_panel.requests_to("/index.html")) == 11:
            closes.append(asyncio.create_task(client.async_close()))
        return FakeResponse(status=500)

    fake_panel.respond_with("/index.html", fail_index_page)

    # The close queues on the lock while the last attempt is in flight, ahead of the
    # cleanup logout of the failed login, which then finds the client closed.
    with pytest.raises(ParadoxClosedError):
        await client.async_login()

    [closing] = closes
    await closing
    assert paths(fake_panel) == [
        "/logout.html",
        "/login_page.html",
        "/default.html",
        *["/index.html"] * 11,
        "/logout.html",
    ]
    assert problems(caplog) == []


@pytest.mark.usefixtures("fast_timing")
async def test_close_waits_for_the_request_in_flight(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/statuslive.html", Hang(), times=1)
    # Taken here, not from the panel's clock: under load the panel sees the request
    # later than the client armed its deadline, which would shorten the wait measured.
    started = asyncio.get_running_loop().time()
    status = asyncio.create_task(client.async_get_status())
    await wait_until(lambda: fake_panel.requests_to("/statuslive.html"))

    await client.async_close()

    with pytest.raises(ParadoxTimeoutError):
        await status
    [logout] = fake_panel.requests_to("/logout.html")
    assert logout.received_at - started >= client_module.REQUEST_TIMEOUT - TIMER_SLACK


@pytest.mark.usefixtures("fast_timing")
async def test_call_waiting_for_the_request_in_flight_fails_when_close_starts(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/statuslive.html", Hang(), times=1)
    in_flight = asyncio.create_task(client.async_get_status())
    await wait_until(lambda: fake_panel.requests_to("/statuslive.html"))
    queued = asyncio.create_task(client.async_keep_alive())
    # One turn of the loop lets the task block on the client's lock before close starts.
    await asyncio.sleep(0)

    await client.async_close()

    with pytest.raises(ParadoxTimeoutError):
        await in_flight
    with pytest.raises(ParadoxClosedError):
        await queued
    assert fake_panel.request_lines == ["GET /statuslive.html", "GET /logout.html"]


async def test_close_cancelled_while_logging_out_can_be_repeated(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/logout.html", Hang(), times=1)
    closing = asyncio.create_task(client.async_close())
    await wait_until(lambda: fake_panel.requests_to("/logout.html"))
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    await client.async_close()

    assert fake_panel.request_lines == ["GET /logout.html", "GET /logout.html"]


async def test_concurrent_closes_send_one_logout(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    await asyncio.gather(client.async_close(), client.async_close())

    assert fake_panel.request_lines == ["GET /logout.html"]


@pytest.mark.usefixtures("fast_timing")
async def test_no_concurrent_close_returns_before_the_logout_reached_the_panel(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/statuslive.html", Hang(), times=1)
    status = asyncio.create_task(client.async_get_status())
    await wait_until(lambda: fake_panel.requests_to("/statuslive.html"))
    closes = [asyncio.create_task(client.async_close()) for _ in range(2)]

    await asyncio.wait(closes, return_when=asyncio.FIRST_COMPLETED)

    assert fake_panel.requests_to("/logout.html")
    await asyncio.gather(*closes)
    with pytest.raises(ParadoxTimeoutError):
        await status
    assert fake_panel.request_lines == ["GET /statuslive.html", "GET /logout.html"]


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        pytest.param(
            FakeResponse(status=500),
            "Logout at close failed: Request failed with status code 500",
            id="HTTP 500",
        ),
        pytest.param(
            Reset(),
            "Logout at close failed: ServerDisconnectedError",
            id="reset connection",
        ),
    ],
)
async def test_failed_logout_at_close_is_only_logged_at_debug(
    fake_panel: FakePanel,
    client: ParadoxClient,
    caplog: pytest.LogCaptureFixture,
    fault: PanelResult,
    message: str,
) -> None:
    fake_panel.respond_with("/logout.html", fault)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        await client.async_close()

    assert [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
    ] == [(logging.DEBUG, message)]
    with pytest.raises(ParadoxClosedError):
        await client.async_get_status()


async def test_close_after_a_failed_logout_sends_nothing_more(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/logout.html", FakeResponse(status=500), times=1)

    await client.async_close()
    await client.async_close()

    assert fake_panel.request_lines == ["GET /logout.html"]


async def test_login_error_of_a_reset_connection_does_not_carry_the_credentials(
    fake_panel: FakePanel, client: ParadoxClient
) -> None:
    fake_panel.respond_with("/default.html", Reset())

    with pytest.raises(ParadoxConnectionError) as caught:
        await client.async_login()

    [first, _resent] = fake_panel.requests_to("/default.html")
    assert first.url == f"/default.html?u={HASHED_U}&p={HASHED_P}"
    assert_no_credentials(caught.value)


async def test_login_error_with_an_aiohttp_url_does_not_carry_the_credentials(
    fake_panel: FakePanel, golden: dict[str, str]
) -> None:
    # The message of a reset connection holds no URL, so only an aiohttp error that
    # does shows whether the translation keeps such an error in the exception chain.
    # A middleware raises it, because subclassing ClientSession emits a
    # DeprecationWarning.
    raised: list[aiohttp.ClientResponseError] = []

    async def fail_default_page(
        request: aiohttp.ClientRequest, handler: aiohttp.ClientHandlerType
    ) -> aiohttp.ClientResponse:
        if request.url.path != "/default.html":
            return await handler(request)
        error = aiohttp.ClientResponseError(
            request.request_info, (), status=502, message="Bad Gateway"
        )
        raised.append(error)
        raise error

    async with aiohttp.ClientSession(middlewares=(fail_default_page,)) as session:
        panel_client = ParadoxClient(
            session, fake_panel.host, golden["username"], golden["password"]
        )

        with pytest.raises(ParadoxConnectionError) as caught:
            await panel_client.async_login()

    [aiohttp_error] = raised
    assert HASHED_U in str(aiohttp_error)
    assert HASHED_P in str(aiohttp_error)
    assert_no_credentials(caught.value)
