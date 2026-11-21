import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import entity_registry as er
from homeassistant.util.async_ import cancelling, get_scheduled_timer_handles
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.paradox.const import DOMAIN
from tests.fake_panel import Hang

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant

    from tests.fake_panel import FakePanel, PanelRequest, PanelResult

TROUBLE_ENTITY_ID: Final = "binary_sensor.paradox_trouble"
SETTLE_ROUNDS: Final = 1000


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int = 1
) -> None:
    """Move the clock on one second at a time, letting every due timer run."""
    for _ in range(seconds):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)


def entity_id_for(
    hass: HomeAssistant, entry: ConfigEntry, platform: Platform, unique_id_suffix: str
) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{unique_id_suffix}"
    )


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int
) -> None:
    """
    Move the clock on like ``tick``, without waiting for background tasks.

    A request the panel hangs, or a new login waiting for its next index page attempt,
    keeps a background task from ever finishing, and ``tick`` would wait for it.
    """
    for _ in range(seconds):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def waits_for_the_clock(task: asyncio.Future[Any]) -> bool:
    """
    Return whether ``task`` waits on a future that a scheduled timer will resolve.

    A task that waits on another task is followed to the future that one waits on, as
    often as it takes. A future that is not a task, such as an executor job's, never
    waits for the clock.
    """
    # Private attributes: asyncio.capture_call_graph shows that a task is in
    # asyncio.sleep, but not whether a live timer will wake it. A single-entity service
    # call waits on the task in which Home Assistant runs the entity's method.
    waiter = getattr(task, "_fut_waiter", None)
    while isinstance(waiter, asyncio.Task):
        waiter = getattr(waiter, "_fut_waiter", None)
    return waiter is not None and any(
        handle._args[:1] == (waiter,)
        for handle in get_scheduled_timer_handles(task.get_loop())
        if not handle.cancelled()
    )


async def settle(hass: HomeAssistant, task: asyncio.Task[Any]) -> None:
    """
    Yield to the event loop until nothing can go on without the clock moving.

    Returns once ``task`` and every future ``hass`` tracks, background tasks included,
    has ended, is being cancelled, or ``waits_for_the_clock``. Unlike
    ``hass.async_block_till_done``, it returns while a service call sleeps on the
    frozen clock. Raises ``AssertionError`` after ``SETTLE_ROUNDS`` turns of the loop
    otherwise, which includes a request the fake panel holds: its task waits for a
    socket, not for the clock.
    """
    for _ in range(SETTLE_ROUNDS):
        if all(
            future.done() or cancelling(future) or waits_for_the_clock(future)
            for future in (task, *hass._tasks, *hass._background_tasks)
        ):
            return
        await asyncio.sleep(0)
    raise AssertionError("The tasks neither ended nor waited for the clock")


async def tick_during(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    task: asyncio.Task[Any],
    seconds: int = 1,
) -> None:
    """
    Move the clock on one second at a time while ``task`` runs, settling before each.

    Each second runs every timer due within the next half second, as ``tick`` does,
    and one more ``settle`` follows the last second.
    """
    for _ in range(seconds):
        # A tick while a request is on its way would fire the request's timeouts.
        await settle(hass, task)
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
    await settle(hass, task)


async def run_while_ticking[T](
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    coro: Coroutine[Any, Any, T],
    seconds: int,
) -> T:
    """
    Run ``coro`` in a task while ``tick_during`` moves the clock on ``seconds``.

    The clock moves on all ``seconds`` even when the task ends sooner. Returns what
    the task returned, raises what it raised, and raises ``AssertionError`` when it is
    still running after ``seconds``. The task is cancelled on the way out.
    """
    task = asyncio.create_task(coro)
    try:
        await tick_during(hass, freezer, task, seconds)
        assert task.done(), f"Still running after {seconds} s"
        return await task
    finally:
        task.cancel()


def hang_once(panel: FakePanel, path: str) -> asyncio.Event:
    arrived = asyncio.Event()

    def hang(_request: PanelRequest) -> PanelResult:
        arrived.set()
        return Hang()

    panel.respond_with(path, hang, times=1)
    return arrived
