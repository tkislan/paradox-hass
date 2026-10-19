import pytest

from custom_components.paradox.panel.errors import (
    ParadoxAuthError,
    ParadoxConnectionError,
    ParadoxError,
    ParadoxHttpError,
    ParadoxTimeoutError,
)
from tests.panel.expected_errors import assert_error

ACCEPTED = [
    (
        ParadoxConnectionError("ClientConnectorError"),
        "connect ECONNREFUSED 127.0.0.1:1",
    ),
    (ParadoxTimeoutError(), "read ECONNRESET"),
]
REJECTED = [
    (ParadoxHttpError(500), "Request failed with status code 404"),
    (
        ParadoxConnectionError("ClientPayloadError"),
        "Request failed with status code 500",
    ),
    (ParadoxAuthError("Login failed!"), "Login failed"),
    (
        ParadoxConnectionError("timeout of 20000ms exceeded"),
        "timeout of 20000ms exceeded",
    ),
    (ParadoxHttpError(500), "socket hang up"),
]


@pytest.mark.parametrize(("exc", "message"), ACCEPTED)
def test_assert_error_accepts_the_matching_failure(
    exc: BaseException, message: str
) -> None:
    assert_error(exc, message)


@pytest.mark.parametrize(("exc", "message"), REJECTED)
def test_assert_error_rejects_any_other_failure(
    exc: BaseException, message: str
) -> None:
    with pytest.raises(AssertionError):
        assert_error(exc, message)


@pytest.mark.parametrize(
    "message",
    [
        "Login failed",
        "Session value not found in login page",
        "Title not found in default page",
        "Regex didn't match the value",
        "Invalid list length, should be divisible by tuple size",
        "Command answered with the login page",
        "Keep-alive answered with the login page",
        "timeout of 20000ms exceeded",
    ],
)
def test_assert_error_rejects_a_bare_paradox_error_for_an_exact_message(
    message: str,
) -> None:
    with pytest.raises(AssertionError) as caught:
        assert_error(ParadoxError(message), message)

    # A misspelt message would be rejected as unknown and pass for the wrong reason.
    assert "no expectation" not in str(caught.value)


def test_assert_error_fails_on_a_message_it_does_not_know() -> None:
    with pytest.raises(AssertionError, match="no expectation"):
        assert_error(ParadoxError("EPIPE"), "EPIPE")
