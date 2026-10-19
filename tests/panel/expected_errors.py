import re
from typing import Final

from custom_components.paradox.panel.errors import (
    ParadoxAuthError,
    ParadoxConnectionError,
    ParadoxError,
    ParadoxHttpError,
    ParadoxPageError,
    ParadoxSessionExpiredError,
    ParadoxTimeoutError,
)

_EXACT_MESSAGES: Final[dict[str, type[ParadoxError]]] = {
    "Login failed": ParadoxAuthError,
    "Session value not found in login page": ParadoxPageError,
    "Title not found in default page": ParadoxPageError,
    "Regex didn't match the value": ParadoxPageError,
    "Invalid list length, should be divisible by tuple size": ParadoxPageError,
    "Command answered with the login page": ParadoxSessionExpiredError,
    "Keep-alive answered with the login page": ParadoxSessionExpiredError,
    "timeout of 20000ms exceeded": ParadoxTimeoutError,
}
_HTTP_STATUS_RE: Final = re.compile(r"Request failed with status code (\d+)")


def assert_error(exc: BaseException, message: str) -> None:
    """
    Assert that ``exc`` is the Python failure for the bridge's error ``message``.

    Axios and Node messages only say that the request failed one way: ``socket hang
    up`` and the ``connect ...`` and ``read ...`` errors accept any
    ``ParadoxConnectionError``. A message the spec tables use but this mapping does
    not know fails, so that a new row cannot pass unchecked.
    """
    if (expected_type := _EXACT_MESSAGES.get(message)) is not None:
        assert isinstance(exc, expected_type), (
            f"{message!r}: expected {expected_type.__name__}, got {exc!r}"
        )
        assert str(exc) == message, f"{message!r}: got message {str(exc)!r}"
    elif status := _HTTP_STATUS_RE.fullmatch(message):
        assert isinstance(exc, ParadoxHttpError), (
            f"{message!r}: expected ParadoxHttpError, got {exc!r}"
        )
        assert exc.status == int(status[1]), f"{message!r}: got status {exc.status}"
    elif message == "socket hang up" or message.startswith(("connect ", "read ")):
        assert isinstance(exc, ParadoxConnectionError), (
            f"{message!r}: expected ParadoxConnectionError, got {exc!r}"
        )
    else:
        raise AssertionError(f"no expectation for the spec error {message!r}")
