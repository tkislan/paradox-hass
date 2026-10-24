from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER, POLL_INTERVAL
from .panel.errors import ParadoxAuthError, ParadoxClosedError, ParadoxError
from .panel.models import PanelLayout, PanelStatus

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .panel.client import ParadoxClient

type ParadoxConfigEntry = ConfigEntry[ParadoxCoordinator]


class ParadoxCoordinator(DataUpdateCoordinator[PanelStatus]):
    config_entry: ParadoxConfigEntry
    layout: PanelLayout

    def __init__(
        self, hass: HomeAssistant, entry: ParadoxConfigEntry, client: ParadoxClient
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
            always_update=False,
        )
        self.client = client
        self._keep_alive_running = False

    async def _async_setup(self) -> None:
        try:
            self.layout = await self.client.async_login()
        except ParadoxAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ParadoxError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_update_data(self) -> PanelStatus:
        try:
            return await self.client.async_get_status()
        except ParadoxClosedError as err:
            # The refresh was already waiting for the client when it closed, and the
            # entry is going away, so a failed poll would only be noise. Without a
            # first poll there is nothing to keep.
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            return self.data
        except ParadoxError as err:
            raise UpdateFailed(str(err)) from err

    async def async_keep_alive(self, _now: datetime) -> None:
        if self._keep_alive_running:
            return
        self._keep_alive_running = True
        try:
            await self.client.async_keep_alive()
        except ParadoxError as err:
            LOGGER.debug("Keep-alive failed: %s", err)
        finally:
            self._keep_alive_running = False
