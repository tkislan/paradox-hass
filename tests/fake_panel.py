"""
Fake Paradox IP100/IP150 web UI, built from the captured pages in ``tests/data/``.

What a real panel answers to a failed login, a command, a keep-alive or an expired
session was never captured, so those bodies are stand-ins that tests must not rely
on. The one guess the client builds on is that an expired session gets the login
page.
"""

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from aiohttp import web

from tests.spec_tables import TESTS_DIR

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

ZONE_SLOTS: Final = 32
_UNUSED_SLOT: Final = (0, " ")
_COMMAND_AREA_CODES: Final = {"r": 2, "s": 5, "p": 4, "d": 1}
_STALL_POLL_SECONDS: Final = 0.01


def _read_page(name: str) -> str:
    return (TESTS_DIR / "data" / f"{name}.html").read_text(encoding="utf-8")


_LOGIN_TEMPLATE: Final = _read_page("login_page")
_DEFAULT_PAGE: Final = _read_page("default_page")
_INDEX_TEMPLATE: Final = _read_page("index_page")
_STATUS_TEMPLATE: Final = _read_page("unarmed")


def _swap(template: str, pattern: str, replacement: str) -> str:
    page, count = re.subn(pattern, lambda _: replacement, template, count=1)
    if not count:
        raise ValueError(f"Template does not contain {pattern}")
    return page


def _swap_array(page: str, name: str, items: str) -> str:
    return _swap(page, rf"{name} = new Array\([^)]*\)", f"{name} = new Array({items})")


def render_login_page(session_value: str) -> str:
    return _swap(_LOGIN_TEMPLATE, r'loginaff\("\w+"', f'loginaff("{session_value}"')


def render_index_page(zones: Sequence[tuple[int, str]]) -> str:
    """Render the index page with ``zones`` padded to 32 slots with unused ones."""
    slots = [zones[i] if i < len(zones) else _UNUSED_SLOT for i in range(ZONE_SLOTS)]
    items = ",".join(f'{areas},"{name}"' for areas, name in slots)
    return _swap_array(_INDEX_TEMPLATE, "tbl_zone", items)


def _render_status_page(
    statuszone: Sequence[int],
    useraccess: Sequence[int],
    alarms: Sequence[str],
    troubles: Sequence[str],
) -> str:
    tables = {
        "tbl_statuszone": ",".join(map(str, statuszone)),
        "tbl_useraccess": ",".join(map(str, useraccess)),
        "tbl_alarmes": ",".join(f'"{alarm}"' for alarm in alarms),
        "tbl_troubles": ",".join(f'"{trouble}"' for trouble in troubles),
    }
    page = _STATUS_TEMPLATE
    for name, items in tables.items():
        page = _swap_array(page, name, items)
    return page


@dataclass(frozen=True, slots=True)
class FakeResponse:
    status: int = 200
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"Content-Type": "text/html"}
    )
    body: str = ""


@dataclass(frozen=True, slots=True)
class Reset:
    """The connection is closed without any response."""


@dataclass(frozen=True, slots=True)
class Hang:
    """The request is never answered."""


@dataclass(frozen=True, slots=True)
class Stall:
    """Status 200, the headers and ``prefix`` are sent, then nothing more."""

    prefix: str


@dataclass(frozen=True, slots=True)
class Cut:
    """
    The body is cut short: the connection is closed after ``prefix``.

    ``status`` and a Content-Length of 1000 are sent before it.
    """

    prefix: str
    status: int = 200


type PanelResult = FakeResponse | Reset | Hang | Stall | Cut


@dataclass(frozen=True, slots=True)
class PanelRequest:
    """
    A request as the panel received it.

    ``url`` is the path with query exactly as sent, ``headers`` are keyed by
    lower-case name, and ``received_at`` is the event loop time of arrival.
    """

    method: str
    url: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    received_at: float = 0.0


type Responder = Callable[[PanelRequest], PanelResult]


@dataclass(kw_only=True)
class FakePanelOptions:
    session_value: str = "91AC25D06A0C26BA"
    credentials: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Accepted ``(u, p)`` per session value; with none every login is rejected."""
    default_page: str = _DEFAULT_PAGE
    zones: Sequence[tuple[int, str]] = field(
        default_factory=lambda: [
            (1, "Predsien"),
            (1, "Lava izba"),
            (1, "Prava izba"),
            (1, "Spalna"),
            (1, "Obyvacka"),
            (1, "Chodba"),
        ]
    )
    statuszone: Sequence[int] = field(default_factory=lambda: [0] * ZONE_SLOTS)
    useraccess: Sequence[int] = field(default_factory=lambda: [1, 0])
    """Area state codes, the first entry is area 1."""
    alarms: Sequence[str] = field(default_factory=list)
    troubles: Sequence[str] = field(default_factory=list)
    require_login: bool = False
    """Answer the login page instead of data pages while logged out."""


@dataclass(slots=True)
class _Override:
    path: str
    result: PanelResult | Responder
    times: int | None


class FakePanel:
    def __init__(self, options: FakePanelOptions | None = None) -> None:
        if options is None:
            options = FakePanelOptions()
        self.host = ""
        self.session_value = options.session_value
        self.credentials = dict(options.credentials)
        self.default_page = options.default_page
        self.zones = list(options.zones)
        self.statuszone = list(options.statuszone)
        self.useraccess = list(options.useraccess)
        self.alarms = list(options.alarms)
        self.troubles = list(options.troubles)
        self.require_login = options.require_login
        self.logged_in = False
        self.requests: list[PanelRequest] = []
        self.stalls_closed_by_client = 0
        self._overrides: list[_Override] = []
        self._released = asyncio.Event()

    @property
    def request_lines(self) -> list[str]:
        """Every request line seen so far, e.g. ``GET /default.html?u=AB&p=CD``."""
        return [f"{request.method} {request.url}" for request in self.requests]

    def requests_to(self, path: str) -> list[PanelRequest]:
        return [request for request in self.requests if request.path == path]

    def set_status(
        self,
        *,
        statuszone: Sequence[int] | None = None,
        useraccess: Sequence[int] | None = None,
        alarms: Sequence[str] | None = None,
        troubles: Sequence[str] | None = None,
    ) -> None:
        if statuszone is not None:
            self.statuszone = list(statuszone)
        if useraccess is not None:
            self.useraccess = list(useraccess)
        if alarms is not None:
            self.alarms = list(alarms)
        if troubles is not None:
            self.troubles = list(troubles)

    def expire_session(self) -> None:
        """Drop the session, as if it timed out or another login took it over."""
        self.logged_in = False

    def respond_with(
        self, path: str, result: PanelResult | Responder, times: int | None = None
    ) -> None:
        """
        Answer the next ``times`` requests for ``path`` with ``result``.

        ``times=None`` answers every request. Overrides for one path are used in the
        order they were added. ``result`` may be a function of the request.
        """
        self._overrides.append(_Override(path, result, times))

    def clear_overrides(self) -> None:
        self._overrides = []

    def release(self) -> None:
        """Let every ``Hang`` and ``Stall`` handler finish."""
        self._released.set()

    def handle(self, request: PanelRequest) -> PanelResult:
        if (override := self._next_override(request)) is not None:
            return override

        match request.path:
            case "/logout.html":
                self.logged_in = False
                body = "<html><body>Logged out</body></html>"
            case "/login_page.html":
                body = self._login_page()
            case "/default.html":
                self.logged_in = self._accepts_credentials(request.query)
                body = self.default_page if self.logged_in else self._login_page()
            case "/index.html":
                body = self._data_page(lambda: render_index_page(self.zones))
            case "/statuslive.html":
                body = self._data_page(lambda: self._status_page(request.query))
            case "/keep_alive.html":
                body = self._data_page(lambda: "")
            case _:
                return FakeResponse(status=404, body="Not found")
        return FakeResponse(body=body)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._serve)
        return app

    def _next_override(self, request: PanelRequest) -> PanelResult | None:
        for override in self._overrides:
            if override.path == request.path and override.times != 0:
                if override.times is not None:
                    override.times -= 1
                result = override.result
                return result(request) if callable(result) else result
        return None

    def _login_page(self) -> str:
        return render_login_page(self.session_value)

    def _accepts_credentials(self, query: Mapping[str, str]) -> bool:
        expected = self.credentials.get(self.session_value)
        return expected is not None and (query.get("u"), query.get("p")) == expected

    def _data_page(self, render: Callable[[], str]) -> str:
        if self.require_login and not self.logged_in:
            return self._login_page()
        return render()

    def _status_page(self, query: Mapping[str, str]) -> str:
        self._apply_command(query)
        return _render_status_page(
            self.statuszone, self.useraccess, self.alarms, self.troubles
        )

    def _apply_command(self, query: Mapping[str, str]) -> None:
        code = _COMMAND_AREA_CODES.get(query.get("value", ""))
        if code is None:
            return
        try:
            index = int(query.get("area", ""))
        except ValueError:
            return
        if 0 <= index < len(self.useraccess):
            self.useraccess[index] = code

    async def _serve(self, request: web.Request) -> web.StreamResponse:
        panel_request = PanelRequest(
            method=request.method,
            url=request.raw_path,
            path=request.path,
            query=dict(request.query.items()),
            headers={name.lower(): value for name, value in request.headers.items()},
            received_at=asyncio.get_running_loop().time(),
        )
        self.requests.append(panel_request)

        result = self.handle(panel_request)
        if isinstance(result, FakeResponse):
            return web.Response(
                body=result.body.encode("utf-8"),
                status=result.status,
                headers=result.headers,
            )
        if isinstance(result, Stall):
            return await self._stall(request, result.prefix)
        if isinstance(result, Cut):
            return await _cut(request, result)
        if isinstance(result, Hang):
            await self._released.wait()
        return _drop_connection(request, web.Response())

    async def _stall(self, request: web.Request, prefix: str) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/html"})
        await response.prepare(request)
        await response.write(prefix.encode("utf-8"))
        # Without handler cancellation aiohttp never tells a handler that its client
        # left, so look at the transport. With it, which aiohttp_server turns on, the
        # handler is cancelled instead.
        try:
            while not (_client_gone(request) or self._released.is_set()):
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._released.wait(), _STALL_POLL_SECONDS)
        except asyncio.CancelledError:
            self.stalls_closed_by_client += 1
            raise
        if _client_gone(request):
            self.stalls_closed_by_client += 1
        return _drop_connection(request, response)


async def _cut(request: web.Request, result: Cut) -> web.StreamResponse:
    response = web.StreamResponse(
        status=result.status,
        headers={"Content-Type": "text/html", "Content-Length": "1000"},
    )
    await response.prepare(request)
    await response.write(result.prefix.encode("utf-8"))
    return _drop_connection(request, response)


def _client_gone(request: web.Request) -> bool:
    return request.transport is None or request.transport.is_closing()


def _drop_connection(
    request: web.Request, response: web.StreamResponse
) -> web.StreamResponse:
    """Close the connection so that aiohttp's write of ``response`` fails quietly."""
    if request.transport is not None:
        request.transport.close()
    return response
