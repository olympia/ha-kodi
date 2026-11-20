"""Fixtures for the Kodi tests."""

import asyncio
from collections.abc import Awaitable, Callable, Generator
import inspect
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

from jsonrpc_base.jsonrpc import TransportError
from pykodi import Kodi
from pykodi.kodi import KodiHTTPConnection, KodiWSConnection
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.kodi.const import CONF_WS_PORT, DOMAIN
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)

from .util import UUID

APPLICATION_PROPERTIES: dict[str, Any] = {
    "version": {"major": 21, "minor": 2},
    "volume": 80,
    "muted": False,
}


@pytest.fixture
def unique_id() -> str | None:
    """Return the unique ID of the config entry."""
    return UUID


@pytest.fixture
def mock_config_entry(unique_id: str | None) -> MockConfigEntry:
    """Return a Kodi config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="name",
        # Fixed to keep the no_unique_id snapshots stable, as Kodi falls back
        # to the entry ID as unique ID
        entry_id="01JZ8Y6K9QW3X5V7T2R4P6N8M0",
        unique_id=unique_id,
        data={
            CONF_NAME: "name",
            CONF_HOST: "1.1.1.1",
            CONF_PORT: 8080,
            CONF_WS_PORT: 9090,
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pass",
            CONF_SSL: False,
        },
    )


@pytest.fixture
def can_subscribe() -> bool:
    """Return whether the connection pushes notifications (websocket)."""
    return False


@pytest.fixture
def mock_http_connection() -> MagicMock:
    """Return the HTTP connection the setup checks the credentials over."""
    return MagicMock(spec=KodiHTTPConnection)


@pytest.fixture
def mock_connection(
    can_subscribe: bool, mock_http_connection: MagicMock
) -> Generator[MagicMock]:
    """Return a connected Kodi connection."""
    connection = MagicMock(
        spec=KodiWSConnection if can_subscribe else KodiHTTPConnection
    )
    connection.connected = True
    connection.can_subscribe = can_subscribe
    subscriptions: dict[str, list[Callable[[str, Any], Any]]] = {}

    def subscribe(
        method: str, handler: Callable[[str, Any], Any]
    ) -> Callable[[], None]:
        """Call the handler when the test sends the notification.

        A test sends a notification by calling its name on the server of
        the connection, for example server.Player.OnPlay. Unlike PyKodi a
        plain handler runs right in that call, a coroutine handler runs as a
        task that the test waits for by awaiting what the call returns, and
        their errors reach the test.
        """
        if not can_subscribe:
            raise NotImplementedError

        def dispatch(sender: str, data: Any) -> Awaitable[list[Any]]:
            results = [subscribed(sender, data) for subscribed in list(handlers)]
            return asyncio.gather(
                *(result for result in results if inspect.isawaitable(result))
            )

        if (handlers := subscriptions.get(method)) is None:
            handlers = subscriptions[method] = []
            *path, name = method.split(".")
            target = connection.server
            for part in path:
                target = getattr(target, part)
            setattr(target, name, dispatch)
        handlers.append(handler)

        def unsubscribe() -> None:
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    connection.subscribe.side_effect = subscribe

    async def close() -> None:
        connection.connected = False

    connection.close.side_effect = close

    def get_kodi_connection(
        host: str, port: int, ws_port: int | None, *args: Any, **kwargs: Any
    ) -> MagicMock:
        return connection if ws_port is not None else mock_http_connection

    with patch(
        "custom_components.kodi.get_kodi_connection",
        side_effect=get_kodi_connection,
    ):
        yield connection


@pytest.fixture
def mock_http_kodi() -> MagicMock:
    """Return the Kodi client of the credential check, which accepts them."""
    kodi = create_autospec(Kodi, instance=True)
    kodi.ping.return_value = True
    return kodi


@pytest.fixture
def mock_kodi(
    mock_connection: MagicMock,
    mock_http_connection: MagicMock,
    mock_http_kodi: MagicMock,
) -> Generator[MagicMock]:
    """Return a Kodi client without active players."""

    def get_application_properties(properties: list[str]) -> dict[str, Any]:
        if not mock_connection.connected:
            raise TransportError("Not connected")
        return {key: APPLICATION_PROPERTIES[key] for key in properties}

    with patch("custom_components.kodi.Kodi", autospec=True) as kodi_class:
        kodi = kodi_class.return_value
        kodi_class.side_effect = lambda connection: (
            mock_http_kodi if connection is mock_http_connection else kodi
        )
        kodi.get_application_properties.side_effect = get_application_properties
        kodi.get_players.return_value = []
        # The image URLs are built from the entry data, like for the real client
        kodi.thumbnail_url.side_effect = Kodi(
            KodiHTTPConnection(
                "1.1.1.1", 8080, "user", "pass", False, None, MagicMock()
            )
        ).thumbnail_url
        yield kodi
