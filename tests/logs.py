import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def problem_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """
    Return the records at WARNING or above, except asyncio's slow-callback notices.

    The event loop runs in debug mode under the Home Assistant test plugin and logs
    "Executing <Task ...> took 0.149 seconds" whenever a busy machine is slow, which
    says nothing about the code under test.
    """
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and not (
            record.name == "asyncio" and record.getMessage().startswith("Executing ")
        )
    ]
