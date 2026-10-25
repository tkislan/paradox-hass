import time
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER, POLL_INTERVAL, RELOGIN_MIN_INTERVAL, UNANSWERED_GRACE
from .panel.errors import (
    ParadoxAuthError,
    ParadoxClosedError,
    ParadoxConnectionError,
    ParadoxError,
)
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
        # Monotonic readings: a change to the system time must not move either count.
        self._last_login_started: float | None = None
        self._unanswered_since: float | None = None

    async def _async_setup(self) -> None:
        try:
            self.layout = await self.client.async_login()
        except ParadoxAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ParadoxError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_update_data(self) -> PanelStatus:
        try:
            status = await self.client.async_get_status()
        except ParadoxClosedError as err:
            # The refresh was already waiting for the client when it closed, and the
            # entry is going away, so a failed poll would only be noise. Without a
            # first poll there is nothing to keep.
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            return self.data
        except ParadoxError as err:
            return await self._async_recover(err)
        self._unanswered_since = None
        return status

    async def _async_recover(self, err: ParadoxError) -> PanelStatus:
        """
        Log in again after the failed poll ``err``, and return the last good data.

        Raises ``UpdateFailed`` when no login may start yet or the login fails, and
        ``ConfigEntryAuthFailed`` when the panel rejects it. A panel that stalls keeps
        its session, so polls it did not answer wait out ``UNANSWERED_GRACE`` first.
        A person using the module's web page is logged out by every login, so none
        starts within ``RELOGIN_MIN_INTERVAL`` of the start of the last.
        """
        now = time.monotonic()
        if isinstance(err, ParadoxConnectionError):
            if self._unanswered_since is None:
                self._unanswered_since = now
            if now - self._unanswered_since < UNANSWERED_GRACE:
                raise UpdateFailed(str(err)) from err
        self._unanswered_since = None
        # Without a first poll there is no data to return after a new login, and the
        # entry would load with entities that have no state, so the setup is retried.
        if self.data is None or (
            self._last_login_started is not None
            and now - self._last_login_started < RELOGIN_MIN_INTERVAL
        ):
            raise UpdateFailed(str(err)) from err

        LOGGER.info("Logging in to the panel again after a failed poll: %s", err)
        self._last_login_started = now
        try:
            layout = await self.client.async_login()
        except ParadoxAuthError as auth_err:
            raise ConfigEntryAuthFailed from auth_err
        except ParadoxClosedError:
            return self.data
        except ParadoxError as login_err:
            raise UpdateFailed(str(login_err)) from login_err
        if layout != self.layout:
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.data

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
