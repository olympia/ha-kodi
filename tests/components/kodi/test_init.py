"""Test the Kodi integration init."""

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

from freezegun.api import FrozenDateTimeFactory
from jsonrpc_base.jsonrpc import ProtocolError, TransportError
from pykodi import CannotConnectError, InvalidAuthError
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.kodi.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SSL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import init_integration, setup_integration
from .util import MockConnection

WATCHDOG_INTERVAL = timedelta(seconds=10)

websocket = pytest.mark.parametrize(
    "can_subscribe", [pytest.param(True, id="websocket")]
)


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Test successful unload of entry."""
    with patch(
        "custom_components.kodi.media_player.async_setup_entry",
        return_value=None,
    ):
        entry = await init_integration(hass)

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize(
    "can_subscribe",
    [pytest.param(True, id="websocket"), pytest.param(False, id="http")],
)
@pytest.mark.usefixtures("mock_kodi")
async def test_setup_rejected_credentials(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_http_kodi: MagicMock,
    can_subscribe: bool,
) -> None:
    """Test setup fails and starts reauth when the web server rejects the login."""
    if can_subscribe:
        mock_connection.connected = False

        async def connect() -> None:
            mock_connection.connected = True

        mock_connection.connect.side_effect = connect
    mock_http_kodi.ping.side_effect = InvalidAuthError

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.reason == "Login to 1.1.1.1 failed"
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == mock_config_entry.entry_id
    mock_connection.close.assert_awaited_once()


@pytest.mark.parametrize(
    "can_subscribe",
    [pytest.param(True, id="websocket"), pytest.param(False, id="http")],
)
@pytest.mark.usefixtures("mock_kodi")
async def test_setup_checks_credentials_over_http(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_http_connection: MagicMock,
    mock_http_kodi: MagicMock,
) -> None:
    """Test setup pings the web server of Kodi with the credentials of the entry."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_SSL: True}
    )
    with patch(
        "custom_components.kodi.get_kodi_connection",
        side_effect=[mock_connection, mock_http_connection],
    ) as get_kodi_connection:
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert get_kodi_connection.call_args_list[1] == call(
        "1.1.1.1",
        8080,
        None,
        "user",
        "pass",
        True,
        session=async_get_clientsession(hass),
    )
    mock_http_kodi.ping.assert_awaited_once()


@pytest.mark.parametrize(
    "can_subscribe",
    [pytest.param(True, id="websocket"), pytest.param(False, id="http")],
)
@pytest.mark.parametrize(
    ("side_effect", "return_value"),
    [
        pytest.param(CannotConnectError("Timeout"), None, id="cannot_connect"),
        pytest.param(
            ProtocolError(-32601, "Method not found"), None, id="protocol_error"
        ),
        pytest.param(None, False, id="no_pong"),
    ],
)
@pytest.mark.usefixtures("mock_kodi", "mock_connection")
async def test_setup_without_credential_check(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_http_kodi: MagicMock,
    side_effect: Exception | None,
    return_value: bool | None,
) -> None:
    """Test setup goes on when the ping fails for another reason than the login."""
    mock_http_kodi.ping.side_effect = side_effect
    mock_http_kodi.ping.return_value = return_value

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress()


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_setup_skips_credentials_of_unreachable_kodi(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_http_kodi: MagicMock,
) -> None:
    """Test setup does not ping the web server of a Kodi it cannot connect to."""
    mock_connection.connected = False
    mock_connection.connect.side_effect = CannotConnectError

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_http_kodi.ping.assert_not_awaited()


@websocket
@pytest.mark.usefixtures("mock_kodi", "mock_connection")
async def test_reauth_after_rejected_credentials(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_http_kodi: MagicMock,
) -> None:
    """Test the entry loads again once reauth gets credentials that work."""
    mock_http_kodi.ping.side_effect = InvalidAuthError
    await setup_integration(hass, mock_config_entry)
    flow = hass.config_entries.flow.async_progress()[0]

    mock_http_kodi.ping.side_effect = None
    with (
        patch("custom_components.kodi.config_flow.Kodi.ping", return_value=True),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_USERNAME: "user", CONF_PASSWORD: "new"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new"
    assert mock_config_entry.state is ConfigEntryState.LOADED


@websocket
async def test_failed_setup_closes_connection(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test a failed setup closes the connection and drops its subscriptions."""
    mock_kodi.get_application_properties.side_effect = lambda properties: {}

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_connection.close.assert_awaited_once()

    await mock_connection.server.System.OnQuit("xbmc", None)

    mock_connection.close.assert_awaited_once()


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_cancelled_setup_closes_connection(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
) -> None:
    """Test a setup that is cancelled while it connects closes the connection."""
    connecting = asyncio.Event()

    async def connect() -> None:
        connecting.set()
        await asyncio.Event().wait()

    mock_connection.connect.side_effect = connect
    mock_config_entry.add_to_hass(hass)
    setup = hass.async_create_task(
        hass.config_entries.async_setup(mock_config_entry.entry_id)
    )
    async with asyncio.timeout(5):
        await connecting.wait()
        setup.cancel()
        await asyncio.wait([setup])
        await hass.async_block_till_done()

    mock_connection.close.assert_awaited_once()


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_home_assistant_stop_closes_connection(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test stopping Home Assistant closes the connection for good."""
    await setup_integration(hass, mock_config_entry)

    await hass.async_stop()

    mock_connection.close.assert_awaited_once()

    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_connection.connect.assert_awaited_once()


@websocket
async def test_home_assistant_stop_during_setup_closes_connection(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test stopping Home Assistant while the setup reads the version."""
    reading = asyncio.Event()
    closed = asyncio.Event()

    async def get_application_properties(properties: list[str]) -> dict:
        reading.set()
        await closed.wait()
        raise TransportError("Connection closed")

    async def close() -> None:
        mock_connection.connected = False
        closed.set()

    mock_kodi.get_application_properties.side_effect = get_application_properties
    mock_connection.close.side_effect = close
    mock_config_entry.add_to_hass(hass)
    setup = hass.async_create_task(
        hass.config_entries.async_setup(mock_config_entry.entry_id)
    )
    async with asyncio.timeout(5):
        await reading.wait()
        hass.set_state(CoreState.stopping)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        assert await setup

    mock_connection.close.assert_awaited_once()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert "Unable to remove unknown job listener" not in caplog.text
    mock_connection.connect.assert_awaited_once()


@websocket
async def test_unload_before_home_assistant_started(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test an entry unloaded during startup does not connect once started."""
    hass.set_state(CoreState.not_running)
    await setup_integration(hass, mock_config_entry)
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_connection.connect.assert_awaited_once()
    mock_kodi.ping.assert_not_awaited()


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_pending_reconnect_does_not_block(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a pending reconnect does not hold up waiting for Home Assistant."""
    await setup_integration(hass, mock_config_entry)
    connecting = asyncio.Event()

    async def connect() -> None:
        connecting.set()
        await asyncio.Event().wait()

    mock_connection.connected = False
    mock_connection.connect.side_effect = connect
    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    async with asyncio.timeout(1):
        await connecting.wait()
        await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_unload_while_reconnecting(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test unloading ends a pending reconnect before it closes the connection."""
    await setup_integration(hass, mock_config_entry)
    connecting = asyncio.Event()
    calls: list[str] = []

    async def connect() -> None:
        connecting.set()
        try:
            await asyncio.Event().wait()
        finally:
            calls.append("connect ended")

    async def close() -> None:
        calls.append("close")

    mock_connection.connected = False
    mock_connection.connect.side_effect = connect
    mock_connection.close.side_effect = close
    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    await asyncio.sleep(0)
    assert connecting.is_set()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert calls == ["connect ended", "close"]


async def test_http_connection_has_no_watchdog(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test an entry without websocket only polls Kodi."""
    await setup_integration(hass, mock_config_entry)
    mock_kodi.get_application_properties.assert_not_awaited()

    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_connection.subscribe.assert_not_called()
    mock_kodi.ping.assert_not_awaited()
