import asyncio
import time
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    COMMAND_ATTEMPT_WINDOW,
    COMMAND_CHECK_INTERVAL,
    COMMAND_DEADLINE,
    DOMAIN,
    LOGGER,
    POLL_INTERVAL,
    RELOGIN_MIN_INTERVAL,
    UNANSWERED_GRACE,
    UNAVAILABLE_AFTER,
)
from .panel.errors import (
    ParadoxAuthError,
    ParadoxClosedError,
    ParadoxConnectionError,
    ParadoxError,
)
from .panel.models import CONFIRMED_AREA_CODES, PanelLayout, PanelStatus

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .panel.client import ParadoxClient
    from .panel.models import PanelCommand

type ParadoxConfigEntry = ConfigEntry[ParadoxCoordinator]


class CommandNotConfirmedError(ParadoxError):
    """No status poll showed the area in the commanded state before the deadline."""


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
        # Monotonic readings: a change to the system time must not move any count.
        self._last_login_started: float | None = None
        self._unanswered_since: float | None = None
        self._last_good_poll_at: float | None = None
        # Home Assistant stops the timed polls after a rejected login but still runs
        # the refreshes that a command or homeassistant.update_entity asks for. Until
        # a poll or a new login succeeds, those must neither make the entities
        # available again nor start the timed polls again. last_update_success
        # cannot serve as this flag, because a cancelled refresh also clears it.
        self._login_rejected = False
        self._good_polls = 0

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
            try:
                recovered = await self._async_recover(err)
            except ConfigEntryAuthFailed:
                self._login_rejected = True
                raise
            except UpdateFailed as update_err:
                # UpdateFailed would make Home Assistant start the timed polls again,
                # and they would log in again 60 s later and be rejected again.
                if self._login_rejected:
                    raise ConfigEntryAuthFailed from update_err
                if (
                    self._last_good_poll_at is None
                    or time.monotonic() - self._last_good_poll_at >= UNAVAILABLE_AFTER
                ):
                    raise
                LOGGER.debug("Keeping the last status after a failed poll: %s", err)
                return self.data
            self._login_rejected = False
            return recovered
        self._unanswered_since = None
        self._last_good_poll_at = time.monotonic()
        self._login_rejected = False
        self._good_polls += 1
        return status

    async def async_send_command(self, area: int, command: PanelCommand) -> None:
        """
        Send ``command`` to the 0-based ``area`` until a status poll confirms it.

        Returns once a good poll that ended after a command request shows the area in
        one of ``CONFIRMED_AREA_CODES[command]``. The command is sent again
        ``COMMAND_ATTEMPT_WINDOW`` after each attempt started, and none is sent once
        ``COMMAND_DEADLINE`` has passed since the call. At the deadline it raises the
        last attempt's request error, or ``CommandNotConfirmedError`` when the panel
        answered that request or the deadline cut it off. Raises ``ParadoxAuthError``
        at once when a poll ends in a new login that the panel rejects, and
        ``ParadoxClosedError`` once the client is closed.
        """
        label = f"Command {command.name.lower()} for area {area + 1}"
        deadline = time.monotonic() + COMMAND_DEADLINE
        attempt = 0
        good_before: int | None = None
        failure: ParadoxError | None = None
        try:
            async with asyncio.timeout(COMMAND_DEADLINE):
                while True:
                    attempt += 1
                    attempt_started = time.monotonic()
                    # Cleared first: an attempt that the deadline cuts off leaves no
                    # error behind.
                    failure = None
                    failure = await self._async_send_attempt(
                        area, command, label, attempt
                    )
                    # A poll still counts when it ends while a later attempt's request
                    # waits for the client: it read the status after the first request.
                    if good_before is None:
                        good_before = self._good_polls
                    # Cancelling the refresh would make every entity unavailable, and
                    # Home Assistant would drop the next service call.
                    read = self.config_entry.async_create_background_task(
                        self.hass, self.async_refresh(), "paradox command status read"
                    )
                    while True:
                        # No new login may start within 60 s of the rejected one and
                        # Home Assistant has stopped the timed polls, so no later
                        # attempt can be confirmed.
                        if self._login_rejected:
                            LOGGER.warning(
                                "%s: stopped after attempt %d: "
                                "the panel rejected the login",
                                label,
                                attempt,
                            )
                            raise ParadoxAuthError("the panel rejected the login")
                        if (
                            self._good_polls > good_before
                            and self.data.area_code(area)
                            in CONFIRMED_AREA_CODES[command]
                        ):
                            LOGGER.info(
                                "%s: confirmed after attempt %d", label, attempt
                            )
                            return
                        left = (
                            attempt_started + COMMAND_ATTEMPT_WINDOW - time.monotonic()
                        )
                        # A timed poll queued behind the command request can confirm it
                        # before this read even starts, and this read can hang for 20 s.
                        if not read.done():
                            await asyncio.wait({read}, timeout=COMMAND_CHECK_INTERVAL)
                        elif left > 0:
                            await asyncio.sleep(min(COMMAND_CHECK_INTERVAL, left))
                        else:
                            break
                    # The wait that ends the last attempt window can wake before the
                    # timeout's callback runs, which would start a fifth attempt.
                    # Under the tests' frozen clock both are due together and the
                    # timeout always runs first, so no test reaches this.
                    if time.monotonic() >= deadline:  # pragma: no cover
                        break
        except TimeoutError:
            pass
        LOGGER.warning(
            "%s: not confirmed within %.0f s after %d attempts",
            label,
            COMMAND_DEADLINE,
            attempt,
        )
        raise failure or CommandNotConfirmedError(
            f"the area did not reach the new state within {COMMAND_DEADLINE:.0f} s"
        )

    async def _async_send_attempt(
        self, area: int, command: PanelCommand, label: str, attempt: int
    ) -> ParadoxError | None:
        try:
            await self.client.async_command(area, command)
        except ParadoxClosedError:
            raise
        except ParadoxError as err:
            LOGGER.warning("%s, attempt %d: failed: %s", label, attempt, err)
            return err
        LOGGER.info("%s, attempt %d: sent", label, attempt)
        return None

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
