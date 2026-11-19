"""The connection of a Kodi config entry."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
import logging
from typing import Any

from aiohttp import ClientError
from jsonrpc_base.jsonrpc import ProtocolError, TransportError
from pykodi import CannotConnectError, InvalidAuthError, Kodi
from pykodi.kodi import KodiHTTPConnection, KodiWSConnection

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started

_LOGGER = logging.getLogger(__name__)

WEBSOCKET_WATCHDOG_INTERVAL = timedelta(seconds=10)
CREDENTIAL_CHECK_INTERVAL = timedelta(minutes=1)

SYSTEM_NOTIFICATIONS = ("System.OnQuit", "System.OnRestart", "System.OnSleep")

type KodiConfigEntry = ConfigEntry[KodiConnectionManager]


def _error_text(error: BaseException) -> str:
    """Return a short text of the error behind a failed Kodi request."""
    # Most pykodi errors carry no text, their cause does
    error = error.__cause__ or error
    if isinstance(error, TransportError) and error.args:
        # jsonrpc passes the network error after the text
        if isinstance(error.args[-1], (ClientError, TimeoutError)):
            error = error.args[-1]
        else:
            return str(error.args[0])
    elif isinstance(error, ProtocolError) and len(error.args) == 3:
        # An error response from Kodi: code, message and the whole response
        return f"{error.args[1]} ({error.args[0]})"
    return str(error) or type(error).__name__


class KodiConnectionManager:
    """Keep the connection to Kodi open while the config entry is loaded.

    Over HTTP there is nothing to keep open. Over WebSocket a watchdog pings
    Kodi and connects again when the connection is lost.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: KodiConfigEntry,
        connection: KodiHTTPConnection | KodiWSConnection,
        kodi: Kodi,
        http_kodi: Kodi,
    ) -> None:
        """Initialize the connection manager."""
        self.hass = hass
        self.entry = entry
        self.connection = connection
        self.kodi = kodi
        self._http_kodi = http_kodi
        # The Kodi version read over WebSocket after the last connect. None
        # until then, and again after Kodi quits or a connect, ping or version
        # request fails.
        self.version: str | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubscribe: list[Callable[[], None]] = []
        self._watchdog_task: asyncio.Task[None] | None = None
        self._unavailable_logged = False
        self._credential_check_task: asyncio.Task[None] | None = None
        self._last_credential_check: float | None = None

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call the listener when the watchdog or Kodi quitting updates the version."""
        self._listeners.append(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.remove(listener)

        return remove_listener

    async def async_setup(self) -> None:
        """Connect to Kodi, an unreachable Kodi is not an error."""
        if self.connection.can_subscribe:
            self._unsubscribe = [
                self.connection.subscribe(notification, self._async_on_quit)
                for notification in SYSTEM_NOTIFICATIONS
            ]
        # Over WebSocket the watchdog connects later, and logs if that fails too
        with suppress(CannotConnectError, TransportError, ProtocolError):
            await self._async_connect()

    async def async_credentials_rejected(self) -> bool:
        """Return whether Kodi rejects the login, and restart the check interval."""
        self._last_credential_check = self.hass.loop.time()
        try:
            await self._http_kodi.ping()
        except InvalidAuthError:
            return True
        except CannotConnectError, ProtocolError:
            # The credentials stay unchecked, as for a Kodi that is off
            pass
        return False

    @callback
    def async_check_credentials(self) -> None:
        """Check the credentials in the background, at most once a minute."""
        if (
            self._credential_check_task is not None
            and not self._credential_check_task.done()
        ) or (
            self._last_credential_check is not None
            and self.hass.loop.time() - self._last_credential_check
            < CREDENTIAL_CHECK_INTERVAL.total_seconds()
        ):
            return
        self._credential_check_task = self.entry.async_create_background_task(
            self.hass, self._async_check_credentials(), "kodi credential check"
        )

    async def _async_check_credentials(self) -> None:
        if (
            await self.async_credentials_rejected()
            and self.entry.state is ConfigEntryState.LOADED
        ):
            self.entry.async_start_reauth(self.hass)

    @callback
    def async_start(self) -> None:
        """Start the watchdog once Home Assistant has started."""
        if self.connection.can_subscribe:
            self.entry.async_on_unload(
                async_at_started(self.hass, self._async_start_watchdog)
            )

    async def async_stop(self, event: Event | None = None) -> None:
        """Stop the watchdog and close the connection."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []
        if self._watchdog_task is not None and not self._watchdog_task.done():
            # A close during a pending connect would leave the new connection open
            self._watchdog_task.cancel()
            await asyncio.wait([self._watchdog_task])
        await self.connection.close()

    @callback
    def _async_start_watchdog(self, hass: HomeAssistant) -> None:
        self._async_run_watchdog()
        self.entry.async_on_unload(
            async_track_time_interval(
                hass,
                self._async_run_watchdog,
                WEBSOCKET_WATCHDOG_INTERVAL,
                cancel_on_shutdown=True,
            )
        )

    @callback
    def _async_run_watchdog(self, now: datetime | None = None) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = self.entry.async_create_background_task(
                self.hass, self._async_watchdog(), "kodi websocket watchdog"
            )

    async def _async_watchdog(self) -> None:
        """Ping Kodi, or connect again if the connection is lost."""
        if not self.connection.connected:
            try:
                await self._async_connect()
            except (CannotConnectError, TransportError, ProtocolError) as error:
                self.async_log_unavailable(error)
                self._async_set_version(None)
                return
        else:
            try:
                await self.kodi.ping()
                if self.version is None:
                    await self._async_read_version()
            except (CannotConnectError, TransportError, ProtocolError) as error:
                self.async_log_unavailable(error)
                self._async_set_version(None)
                await self.connection.close()
                return
        self.async_log_available()
        # Kodi does not check credentials over WebSocket
        self.async_check_credentials()

    async def _async_connect(self) -> None:
        await self.connection.connect()
        if self.connection.can_subscribe:
            await self._async_read_version()

    async def _async_read_version(self) -> None:
        version = (await self.kodi.get_application_properties(["version"]))["version"]
        self._async_set_version(f"{version['major']}.{version['minor']}")

    async def _async_on_quit(self, sender: str, data: Any) -> None:
        """Close the connection when Kodi quits, restarts or sleeps."""
        self._async_set_version(None)
        await self.connection.close()

    @callback
    def _async_set_version(self, version: str | None) -> None:
        self.version = version
        for listener in list(self._listeners):
            listener()

    @callback
    def async_log_unavailable(self, error: Exception) -> None:
        """Log the first failure of an outage."""
        if not self._unavailable_logged:
            self._unavailable_logged = True
            _LOGGER.info(
                "Kodi at %s is unavailable: %s",
                self.entry.data[CONF_HOST],
                _error_text(error),
            )

    @callback
    def async_log_available(self) -> None:
        """Log the end of an outage."""
        if self._unavailable_logged:
            self._unavailable_logged = False
            _LOGGER.info("Kodi at %s is available again", self.entry.data[CONF_HOST])
