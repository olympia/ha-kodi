"""The coordinator of the Kodi stream sensors."""

from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, override

from jsonrpc_base.jsonrpc import ProtocolError, TransportError

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .connection import KodiConfigEntry, _error_text
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=10)
# The default cooldown of 10 s would delay a stream change right after a refresh
REQUEST_REFRESH_COOLDOWN = 1


@dataclass(frozen=True)
class KodiStreamData:
    """The streams Kodi plays."""

    # The stream info of the video player, None when no video plays
    video: dict[str, Any] | None


class KodiStreamCoordinator(DataUpdateCoordinator[KodiStreamData | None]):
    """Read the streams of the video player of Kodi.

    The data is None while Kodi is off or cannot be reached.
    """

    config_entry: KodiConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: KodiConfigEntry) -> None:
        """Initialize the coordinator."""
        self._manager = config_entry.runtime_data
        self._connection = self._manager.connection
        self._kodi = self._manager.kodi
        # Raised when the streams change without a refresh, so the replies of
        # a refresh that started before are dropped
        self._generation = 0
        self._refresh_requested = False
        self._refresh_debouncer: Debouncer[Coroutine[Any, Any, None]] = Debouncer(
            hass, _LOGGER, cooldown=REQUEST_REFRESH_COOLDOWN, immediate=True
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=None if self._connection.can_subscribe else SCAN_INTERVAL,
            request_refresh_debouncer=self._refresh_debouncer,
            always_update=False,
        )

    @callback
    def async_start(self) -> None:
        """Follow the notifications of Kodi over WebSocket."""
        if not self._connection.can_subscribe:
            return

        for notification, handler in (
            ("Player.OnAVStart", self._async_on_av_change),
            ("Player.OnAVChange", self._async_on_av_change),
            ("Player.OnStop", self._async_on_stop),
        ):
            self.config_entry.async_on_unload(
                self._connection.subscribe(notification, handler)
            )
        self.config_entry.async_on_unload(
            self._manager.async_add_listener(self._async_connection_changed)
        )

        if self._manager.version is not None:
            self._async_request_refresh()

    @callback
    def _async_request_refresh(self) -> None:
        self._refresh_requested = True
        self._refresh_debouncer.async_schedule_call()

    @override
    async def async_request_refresh(self) -> None:
        """Request a refresh, also while one runs."""
        self._refresh_requested = True
        await super().async_request_refresh()

    @override
    async def async_shutdown(self) -> None:
        """Stop the coordinator, and drop the replies of a running refresh."""
        self._generation += 1
        await super().async_shutdown()

    @callback
    def _async_on_av_change(self, sender: str, data: Any) -> None:
        self._async_request_refresh()

    @callback
    def _async_on_stop(self, sender: str, data: Any) -> None:
        # The state stays unknown while Kodi is off
        if self._manager.version is None:
            return
        self._generation += 1
        self.async_set_updated_data(KodiStreamData(None))

    @callback
    def _async_connection_changed(self) -> None:
        if self._manager.version is not None:
            self._async_request_refresh()
            return
        self._generation += 1
        self.async_set_updated_data(None)

    @override
    async def _async_update_data(self) -> KodiStreamData | None:
        """Read the streams of the video player."""
        self._refresh_requested = False
        generation = self._generation
        try:
            data = await self._async_read_streams(generation)
        except (TransportError, ProtocolError) as error:
            # The watchdog and the poll of the media player log an unavailable Kodi
            _LOGGER.debug(
                "Error reading the streams of %s: %s",
                self.config_entry.title,
                _error_text(error),
            )
            data = None

        # The debouncer drops a request during a refresh that outlasts its cooldown
        if self._refresh_requested:
            self._refresh_debouncer.async_schedule_call()
        if generation != self._generation:
            return self.data
        return data

    async def _async_read_streams(self, generation: int) -> KodiStreamData | None:
        players = await self._kodi.get_players()
        # The reply is dropped, so the stream info is not worth a request
        if generation != self._generation:
            return None
        for player in players:
            if player["type"] == "video":
                return KodiStreamData(await self._kodi.get_stream_info(player))
        return KodiStreamData(None)
