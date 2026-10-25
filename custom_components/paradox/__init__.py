from typing import TYPE_CHECKING

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import KEEP_ALIVE_INTERVAL
from .coordinator import ParadoxConfigEntry, ParadoxCoordinator
from .panel.client import ParadoxClient

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant

PLATFORMS: list[Platform] = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ParadoxConfigEntry) -> bool:
    client = ParadoxClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = ParadoxCoordinator(hass, entry, client)

    async def close_client(_event: Event) -> None:
        await client.async_close()

    # Entries are not unloaded when Home Assistant stops, and the panel keeps the
    # session open, locking out every other client, until it is logged out.
    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, close_client))

    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator

        entry.async_on_unload(
            async_track_time_interval(
                hass,
                coordinator.async_keep_alive,
                KEEP_ALIVE_INTERVAL,
                cancel_on_shutdown=True,
            )
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await client.async_close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ParadoxConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_close()
    return unloaded
