import logging

import pytest

from tests.logs import problem_records


def test_slow_callback_notice_of_the_event_loop_is_no_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("asyncio").warning(
        "Executing <Task pending name='Task-1'> took 0.149 seconds"
    )

    assert problem_records(caplog) == []


@pytest.mark.parametrize(
    ("logger", "level", "message"),
    [
        ("asyncio", logging.ERROR, "Task exception was never retrieved"),
        ("asyncio", logging.WARNING, "socket.send() raised exception."),
        ("aiohttp.server", logging.ERROR, "Error handling request"),
        ("custom_components.paradox.panel.client", logging.WARNING, "Logout failed"),
    ],
)
def test_other_warnings_and_errors_are_problems(
    caplog: pytest.LogCaptureFixture, logger: str, level: int, message: str
) -> None:
    logging.getLogger(logger).log(level, message)

    assert [(r.name, r.levelno, r.getMessage()) for r in problem_records(caplog)] == [
        (logger, level, message)
    ]


def test_records_below_warning_are_no_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("aiohttp.access").info("GET /")

    assert problem_records(caplog) == []
