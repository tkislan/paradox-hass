from dataclasses import dataclass
from typing import Any

import pytest

from custom_components.paradox.panel.client import retry
from custom_components.paradox.panel.errors import ParadoxClosedError, ParadoxError
from tests.spec_tables import spec_rows

RETRY_KEYS = {"max_retries", "wait_ms", "attempts", "expected"}


@dataclass(frozen=True)
class Played:
    call_at_ms: list[int]
    settled_at_ms: int
    outcome: dict[str, Any]


async def play(row: dict[str, Any]) -> Played:
    now_ms = 0
    call_at_ms: list[int] = []
    attempts = iter(row["attempts"])

    async def sleep(seconds: float) -> None:
        nonlocal now_ms
        now_ms += round(seconds * 1000)

    async def func() -> Any:
        call_at_ms.append(now_ms)
        attempt = next(attempts)
        if "throws" in attempt:
            raise ParadoxError(attempt["throws"])
        return attempt["returns"]

    outcome: dict[str, Any]
    try:
        outcome = {
            "returns": await retry(
                row["max_retries"], row["wait_ms"] / 1000, func, sleep=sleep
            )
        }
    except ParadoxError as error:
        outcome = {"throws": str(error)}
    return Played(call_at_ms, now_ms, outcome)


@pytest.mark.parametrize("row", spec_rows("util", "retry", RETRY_KEYS))
async def test_retry(row: dict[str, Any]) -> None:
    expected = row["expected"]

    played = await play(row)

    assert played.call_at_ms == expected["call_at_ms"]
    assert played.settled_at_ms == expected["call_at_ms"][-1]
    assert played.outcome == {
        key: value for key, value in expected.items() if key != "call_at_ms"
    }


async def test_retry_raises_a_closed_error_at_once() -> None:
    calls = 0

    async def func() -> None:
        nonlocal calls
        calls += 1
        raise ParadoxClosedError("closed")

    with pytest.raises(ParadoxClosedError):
        await retry(3, 0, func)

    assert calls == 1


async def test_retry_does_not_retry_other_exceptions() -> None:
    calls = 0

    async def func() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("not a panel failure")

    with pytest.raises(ValueError, match="not a panel failure"):
        await retry(3, 0, func)

    assert calls == 1
