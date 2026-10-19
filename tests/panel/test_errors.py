import pytest

from custom_components.paradox.panel.errors import (
    ParadoxAuthError,
    ParadoxClosedError,
    ParadoxConnectionError,
    ParadoxError,
    ParadoxHttpError,
    ParadoxPageError,
    ParadoxSessionExpiredError,
    ParadoxTimeoutError,
)


# The Home Assistant coordinator tells a panel that did not answer from one that
# answered with a failure by catching ParadoxConnectionError, so every other error
# must stay outside it.
@pytest.mark.parametrize(
    ("error", "parent"),
    [
        (ParadoxConnectionError, ParadoxError),
        (ParadoxHttpError, ParadoxError),
        (ParadoxAuthError, ParadoxError),
        (ParadoxSessionExpiredError, ParadoxError),
        (ParadoxClosedError, ParadoxError),
        (ParadoxPageError, ParadoxError),
        (ParadoxTimeoutError, ParadoxConnectionError),
    ],
)
def test_error_has_exactly_one_parent(
    error: type[ParadoxError], parent: type[ParadoxError]
) -> None:
    assert error.__bases__ == (parent,)
