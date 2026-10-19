"""
Async client for the web module of a Paradox IP100/IP150, ported from the bridge.

The panel keeps one session, for whoever logged in last, so the client sends one
request at a time and bounds each by ``REQUEST_TIMEOUT`` over the whole exchange.
Errors keep a request the panel never answered (``ParadoxConnectionError``) apart
from an answer that failed, and none of them carries the request URL, whose query
holds the hashed credentials.
"""

import asyncio
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

import aiohttp

from .crypto import encrypt_credentials
from .errors import (
    ParadoxAuthError,
    ParadoxClosedError,
    ParadoxConnectionError,
    ParadoxError,
    ParadoxHttpError,
    ParadoxPageError,
    ParadoxSessionExpiredError,
    ParadoxTimeoutError,
)
from .pages import (
    is_login_page,
    parse_index_page,
    parse_session_value,
    parse_status_page,
    parse_title,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from .models import PanelCommand, PanelLayout, PanelStatus

# The panel stops accepting connections for 7 to 10 s at a time. A connection held up
# past Linux's SYN retry at 7 s gets through only with the next one at 15 s, and the
# exchange after that needs a few seconds more.
REQUEST_TIMEOUT: Final = 20.0
INDEX_RETRIES: Final = 10
INDEX_RETRY_WAIT: Final = 1.0
LOGIN_TITLE: Final = "Paradox IP Module"
BROWSER_HEADERS: Final = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-GB,en;q=0.9,en-US;q=0.8,sk;q=0.7",
    "Connection": "keep-alive",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_2) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36"
    ),
}

_LOGGER = logging.getLogger(__name__)


async def retry[T](
    retries: int,
    wait: float,
    func: Callable[[], Awaitable[T]],
    *,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> T:
    """
    Return the result of ``func``, calling it again after ``wait`` seconds on failure.

    A ``ParadoxError`` other than ``ParadoxClosedError`` is retried at most
    ``retries`` more times, and the last one is raised. There is no wait after the
    last attempt. Any other exception propagates at once.
    """
    attempt = 0
    while True:
        try:
            return await func()
        except ParadoxClosedError:
            raise
        except ParadoxError:
            if attempt >= retries:
                raise
        attempt += 1
        await sleep(wait)


def _decode(body: bytes, encoding: str) -> str:
    # aiohttp falls back to UTF-8 for a charset it does not know, but not for a codec
    # that cannot decode text (``hex``, ``rot13``, ``undefined``): decoding raises.
    try:
        return body.decode(encoding, errors="replace")
    except LookupError, UnicodeError:
        return body.decode("utf-8", errors="replace")


class ParadoxClient:
    """
    Client for one panel.

    The caller owns ``session`` and closes it after ``async_close``.
    """

    def __init__(
        self, session: aiohttp.ClientSession, host: str, username: str, password: str
    ) -> None:
        self._session = session
        self._host = host
        self._username = username
        self._password = password
        self._browser_headers = {
            **BROWSER_HEADERS,
            "Referer": f"http://{host}/login.html",
        }
        self._lock = asyncio.Lock()
        self._closed = False
        self._logout_attempted = False

    async def async_login(self) -> PanelLayout:
        """
        Log in and return the zone layout of the index page.

        Raises ``ParadoxAuthError`` when the panel rejects the credentials,
        ``ParadoxPageError`` for a page that cannot be used, ``ParadoxConnectionError``
        and ``ParadoxHttpError`` for a request that failed, and ``ParadoxClosedError``
        once the client is closed.
        """
        await self._logout_or_warn("Logout before login failed")

        login_page = await self._request("/login_page.html", browser_headers=True)
        session_value = parse_session_value(login_page)
        credentials: tuple[str, str] | None
        # Raised outside the handler: inside it, the ValueError and the key in its
        # traceback frames would stay in ``__context__``.
        try:
            credentials = encrypt_credentials(
                session_value, self._username, self._password
            )
        except ValueError:
            credentials = None
        if credentials is None:
            raise ParadoxPageError("Session value too long")
        u, p = credentials

        default_page = await self._request(
            "/default.html", {"u": u, "p": p}, browser_headers=True
        )
        if parse_title(default_page) != LOGIN_TITLE:
            raise ParadoxAuthError("Login failed")

        try:
            index_page = await retry(
                INDEX_RETRIES,
                INDEX_RETRY_WAIT,
                lambda: self._request("/index.html", browser_headers=True),
            )
            return parse_index_page(index_page)
        except ParadoxClosedError:
            raise
        except ParadoxError:
            # The panel serves one session: an open one locks out every other client
            # until it times out.
            await self._logout_or_warn("Logout after failed login failed")
            raise

    async def async_logout(self) -> None:
        """
        End the session.

        Raises ``ParadoxConnectionError`` or ``ParadoxHttpError`` when the request
        fails, and ``ParadoxClosedError`` once the client is closed.
        """
        await self._request("/logout.html")

    async def async_get_status(self) -> PanelStatus:
        """
        Return the tables of the status page.

        Raises ``ParadoxConnectionError`` when the panel does not answer,
        ``ParadoxHttpError`` for an HTTP error status, ``ParadoxPageError`` for a page
        without the status tables (the login page included) and
        ``ParadoxClosedError`` once the client is closed.
        """
        return parse_status_page(await self._request("/statuslive.html"))

    async def async_keep_alive(self) -> None:
        """
        Send a keep-alive.

        Raises ``ParadoxSessionExpiredError`` when the panel answers with its login
        page, otherwise the errors of ``async_logout``.
        """
        page = await self._request("/keep_alive.html", {"msgid": "1"})
        if is_login_page(page):
            raise ParadoxSessionExpiredError("Keep-alive answered with the login page")

    async def async_command(self, area: int, command: PanelCommand) -> None:
        """
        Send ``command`` to the 0-based ``area``.

        Raises ``ParadoxSessionExpiredError`` when the panel answers with its login
        page, otherwise the errors of ``async_logout``.
        """
        page = await self._request(
            "/statuslive.html", {"area": f"{area:02d}", "value": command.value}
        )
        if is_login_page(page):
            raise ParadoxSessionExpiredError("Command answered with the login page")

    async def async_close(self) -> None:
        """
        Close the client and log out, after the request in flight finishes.

        Every later call raises ``ParadoxClosedError``. A failed logout is only
        logged. Closing again, also while another close waits, returns once the
        logout was attempted, so a close that was cancelled can be repeated.
        """
        self._closed = True
        async with self._lock:
            if self._logout_attempted:
                return
            try:
                await self._send("/logout.html", None, browser_headers=False)
            except ParadoxError as error:
                _LOGGER.debug("Logout at close failed: %s", error)
            self._logout_attempted = True

    async def _logout_or_warn(self, warning: str) -> None:
        try:
            await self.async_logout()
        except ParadoxClosedError:
            raise
        except ParadoxError:
            _LOGGER.warning(warning)

    async def _request(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        browser_headers: bool = False,
    ) -> str:
        async with self._lock:
            if self._closed:
                raise ParadoxClosedError("Client is closed")
            return await self._send(path, params, browser_headers=browser_headers)

    async def _send(
        self,
        path: str,
        params: Mapping[str, str] | None,
        *,
        browser_headers: bool,
    ) -> str:
        error: ParadoxError
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.get(
                    f"http://{self._host}{path}",
                    params=params,
                    headers=self._browser_headers if browser_headers else None,
                )
                # The body is read before the status is judged: an error status whose
                # body is cut short is a request the panel never answered.
                body = await response.read()
        except TimeoutError:
            error = ParadoxTimeoutError()
        except (aiohttp.ClientError, OSError) as exc:
            # Only the class name: aiohttp's messages carry the request URL, which
            # holds the hashed credentials.
            error = ParadoxConnectionError(type(exc).__name__)
        else:
            if HTTPStatus.OK <= response.status < HTTPStatus.MULTIPLE_CHOICES:
                return _decode(body, response.get_encoding())
            error = ParadoxHttpError(response.status)
        # Raised outside the handlers: inside one, ``from None`` would still leave the
        # aiohttp exception in ``__context__``.
        raise error from None
