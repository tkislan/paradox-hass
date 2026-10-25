import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.paradox.const import DOMAIN
from tests.fake_panel import Hang

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant

    from tests.fake_panel import FakePanel, PanelRequest, PanelResult


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


def hang_once(panel: FakePanel, path: str) -> asyncio.Event:
    arrived = asyncio.Event()

    def hang(_request: PanelRequest) -> PanelResult:
        arrived.set()
        return Hang()

    panel.respond_with(path, hang, times=1)
    return arrived
