"""Tests for the Kodi sensors."""

import asyncio
from collections.abc import Awaitable, Callable, Generator
from datetime import timedelta
import logging
from typing import Any
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from jsonrpc_base.jsonrpc import ProtocolError, TransportError
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.kodi.const import DOMAIN
from homeassistant.const import CONF_NAME, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from . import (
    AUDIO_PLAYER,
    PICTURE_PLAYER,
    VIDEO_PLAYER,
    set_idle,
    set_playing,
    setup_integration,
)
from .util import UUID

ENTITY_ID = "sensor.name_dynamic_range"
COORDINATOR_LOGGER = "custom_components.kodi.coordinator"

SCAN_INTERVAL = timedelta(seconds=10)
REQUEST_REFRESH_COOLDOWN = timedelta(seconds=1)
WATCHDOG_INTERVAL = timedelta(seconds=10)

websocket = pytest.mark.parametrize(
    "can_subscribe", [pytest.param(True, id="websocket")]
)


@pytest.fixture(autouse=True)
def platforms() -> Generator[None]:
    """Set up only the sensor platform."""
    with patch("custom_components.kodi.PLATFORMS", [Platform.SENSOR]):
        yield


def stream_info(hdr_type: str) -> dict[str, Any]:
    """Return the stream info of a video with the given HDR type of Kodi."""
    return {
        "currentvideostream": {"codec": "hevc", "index": 0},
        "videostreams": [{"codec": "hevc", "index": 0}],
        "infolabels": {"VideoPlayer.HdrType": hdr_type},
        "hdrhints": [],
    }


async def set_video(hass: HomeAssistant, mock_kodi: MagicMock, hdr_type: str) -> None:
    """Make Kodi play a video with the given HDR type."""
    await set_playing(hass, mock_kodi)
    mock_kodi.get_stream_info.return_value = stream_info(hdr_type)


async def advance_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, interval: timedelta
) -> None:
    """Let the given time pass."""
    freezer.tick(interval)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def av_start(connection: MagicMock) -> None:
    """Let Kodi report that a stream started."""
    await connection.server.Player.OnAVStart(
        "xbmc", {"item": {"id": 1, "type": "movie"}, "player": {"playerid": 1}}
    )


async def av_change(connection: MagicMock) -> None:
    """Let Kodi report that a stream changed."""
    await connection.server.Player.OnAVChange(
        "xbmc", {"item": {"id": 1, "type": "movie"}, "player": {"playerid": 1}}
    )


async def stop_playback(connection: MagicMock) -> None:
    """Let Kodi report that the playback stopped."""
    await connection.server.Player.OnStop(
        "xbmc", {"end": False, "item": {"id": 1, "type": "movie"}}
    )


async def quit_kodi(connection: MagicMock) -> None:
    """Let Kodi report that it quits."""
    await connection.server.System.OnQuit("xbmc", None)


async def update_entity(hass: HomeAssistant, connection: MagicMock) -> None:
    """Ask for an update of the sensor while a refresh runs."""
    assert await async_setup_component(hass, "homeassistant", {})
    # Returns once the coordinator has the request, the refresh comes later
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": ENTITY_ID}, blocking=True
    )


async def stream_change(hass: HomeAssistant, connection: MagicMock) -> None:
    """Let Kodi report a changed stream."""
    await av_change(connection)


@websocket
@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    "unique_id",
    [pytest.param(UUID, id="unique_id"), pytest.param(None, id="no_unique_id")],
)
async def test_entity_and_device(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the registered sensor, its state and the device of Kodi."""
    await set_video(hass, mock_kodi, "hdr10")

    await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)
    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert devices == snapshot(name="devices")


@pytest.mark.usefixtures("mock_connection", "mock_kodi")
@pytest.mark.parametrize(
    ("name", "title", "expected_name"),
    [
        pytest.param(None, "Living room", "Living room", id="added_by_hand"),
        pytest.param("cinema", "Living room", "cinema", id="discovered"),
    ],
)
async def test_device_name_after_reload(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    name: str | None,
    title: str,
    expected_name: str,
) -> None:
    """Test the sensor keeps the name of the device, or the entry title without one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={**mock_config_entry.data, CONF_NAME: name},
    )
    await setup_integration(hass, entry)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    (device,) = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert device.name == expected_name


@websocket
@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("hdr_type", "expected_state"),
    [
        pytest.param("", "sdr", id="sdr"),
        pytest.param("hdr10", "hdr10", id="hdr10"),
        pytest.param("hdr10plus", "hdr10plus", id="hdr10plus"),
        pytest.param("dolbyvision", "dolbyvision", id="dolbyvision"),
        pytest.param("hlg", "hlg", id="hlg"),
        pytest.param("hdrvivid", STATE_UNKNOWN, id="unknown_type"),
    ],
)
async def test_dynamic_range(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    hdr_type: str,
    expected_state: str,
) -> None:
    """Test the dynamic range follows the HDR type of the playing video."""
    await set_video(hass, mock_kodi, hdr_type)

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == expected_state
    mock_kodi.get_stream_info.assert_awaited_once_with(VIDEO_PLAYER)


@websocket
@pytest.mark.usefixtures("mock_connection")
async def test_dynamic_range_of_video_player(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
) -> None:
    """Test the streams are read from the video player when more players play."""
    await set_video(hass, mock_kodi, "dolbyvision")
    mock_kodi.get_players.return_value = [AUDIO_PLAYER, VIDEO_PLAYER]

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == "dolbyvision"
    mock_kodi.get_stream_info.assert_awaited_once_with(VIDEO_PLAYER)


@websocket
@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    "players",
    [
        pytest.param([], id="idle"),
        pytest.param([AUDIO_PLAYER], id="music"),
        pytest.param([PICTURE_PLAYER], id="pictures"),
    ],
)
async def test_sdr_without_video(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    players: list[dict[str, Any]],
) -> None:
    """Test the dynamic range is SDR while no video plays."""
    mock_kodi.get_players.return_value = players

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == "sdr"
    mock_kodi.get_stream_info.assert_not_awaited()


@websocket
async def test_unknown_while_kodi_is_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test the dynamic range is unknown while Kodi is off."""
    mock_connection.connected = False
    await set_video(hass, mock_kodi, "hdr10")

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN
    mock_kodi.get_players.assert_not_awaited()


@websocket
@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("request_name", "error", "error_text"),
    [
        pytest.param(
            "get_players",
            TransportError("Connection lost"),
            "Connection lost",
            id="players",
        ),
        pytest.param(
            "get_stream_info",
            ProtocolError(-32602, "Invalid params.", {}),
            "Invalid params. (-32602)",
            id="stream_info",
        ),
    ],
)
async def test_unknown_after_failed_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    caplog: pytest.LogCaptureFixture,
    request_name: str,
    error: Exception,
    error_text: str,
) -> None:
    """Test the dynamic range is unknown when Kodi does not answer, logged at debug."""
    caplog.set_level(logging.DEBUG, logger=COORDINATOR_LOGGER)
    await set_video(hass, mock_kodi, "hdr10")
    getattr(mock_kodi, request_name).side_effect = error

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN
    records = [record for record in caplog.records if record.name == COORDINATOR_LOGGER]
    assert {record.levelno for record in records} == {logging.DEBUG}
    assert f"Error reading the streams of name: {error_text}" in [
        record.getMessage() for record in records
    ]


@websocket
@pytest.mark.parametrize("notification", [av_start, av_change])
async def test_refresh_on_stream_change(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    notification: Callable[[MagicMock], Awaitable[None]],
) -> None:
    """Test a started or changed stream refreshes the dynamic range at once."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == "sdr"
    await advance_time(hass, freezer, REQUEST_REFRESH_COOLDOWN)
    await set_video(hass, mock_kodi, "hdr10")

    await notification(mock_connection)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "hdr10"


@websocket
async def test_refresh_after_cooldown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a stream change right after a refresh is read once the cooldown ends."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    mock_kodi.get_stream_info.return_value = stream_info("dolbyvision")

    await av_change(mock_connection)
    await av_change(mock_connection)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "hdr10"

    await advance_time(hass, freezer, REQUEST_REFRESH_COOLDOWN)

    assert hass.states.get(ENTITY_ID).state == "dolbyvision"
    assert mock_kodi.get_stream_info.await_count == 2


@websocket
async def test_unchanged_streams_do_not_write_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a refresh that reads the same streams leaves the state alone."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    last_reported = hass.states.get(ENTITY_ID).last_reported
    await advance_time(hass, freezer, REQUEST_REFRESH_COOLDOWN)

    await av_change(mock_connection)
    await hass.async_block_till_done()

    assert mock_kodi.get_stream_info.await_count == 2
    assert hass.states.get(ENTITY_ID).last_reported == last_reported


@websocket
async def test_sdr_after_stop(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test the dynamic range is SDR once the playback stops."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)

    await stop_playback(mock_connection)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "sdr"
    mock_kodi.get_stream_info.assert_awaited_once()


@websocket
async def test_sdr_after_stop_following_failed_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test a stop gives SDR also when the refresh before it failed."""
    await set_video(hass, mock_kodi, "hdr10")
    mock_kodi.get_stream_info.side_effect = TransportError("Connection lost")
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN

    await stop_playback(mock_connection)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "sdr"


@websocket
async def test_unknown_after_quit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test the dynamic range is unknown after Kodi quits, also after its OnStop."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)

    await quit_kodi(mock_connection)
    await stop_playback(mock_connection)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN


@websocket
@pytest.mark.parametrize(
    ("notification", "expected_state"),
    [
        pytest.param(stop_playback, "sdr", id="stop"),
        pytest.param(quit_kodi, STATE_UNKNOWN, id="quit"),
    ],
)
async def test_reset_during_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    notification: Callable[[MagicMock], Awaitable[None]],
    expected_state: str,
) -> None:
    """Test a refresh that started before a stop or quit does not bring back HDR."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    await advance_time(hass, freezer, REQUEST_REFRESH_COOLDOWN)
    requested = asyncio.Event()
    replied = asyncio.Event()

    async def pending_request(player: dict[str, Any]) -> dict[str, Any]:
        requested.set()
        await replied.wait()
        return stream_info("dolbyvision")

    mock_kodi.get_stream_info.side_effect = pending_request

    async with asyncio.timeout(5):
        await av_change(mock_connection)
        await requested.wait()
        await notification(mock_connection)
        assert hass.states.get(ENTITY_ID).state == expected_state
        replied.set()
        await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == expected_state


@websocket
async def test_stop_during_refresh_after_reconnect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a stop during the refresh after a reconnect does not bring back HDR."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connect.side_effect = connect
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    await quit_kodi(mock_connection)
    await hass.async_block_till_done()
    requested = asyncio.Event()
    replied = asyncio.Event()

    async def pending_request(player: dict[str, Any]) -> dict[str, Any]:
        requested.set()
        await replied.wait()
        return stream_info("hdr10")

    mock_kodi.get_stream_info.side_effect = pending_request

    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    async with asyncio.timeout(5):
        await requested.wait()
        assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN
        await stop_playback(mock_connection)
        assert hass.states.get(ENTITY_ID).state == "sdr"
        replied.set()
        await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "sdr"


@websocket
@pytest.mark.parametrize(
    "request_refresh",
    [
        pytest.param(stream_change, id="stream_change"),
        pytest.param(update_entity, id="update_entity"),
    ],
)
@pytest.mark.parametrize(
    ("first_reply", "first_state"),
    [
        pytest.param(stream_info("hdr10"), "hdr10", id="read"),
        pytest.param(
            ProtocolError(-32602, "Invalid params.", {}), STATE_UNKNOWN, id="failed"
        ),
    ],
)
async def test_request_during_slow_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    request_refresh: Callable[[HomeAssistant, MagicMock], Awaitable[None]],
    first_reply: dict[str, Any] | Exception,
    first_state: str,
) -> None:
    """Test a request during a refresh that outlasts the cooldown is not lost."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    await advance_time(hass, freezer, REQUEST_REFRESH_COOLDOWN)
    requested = asyncio.Event()
    replied = asyncio.Event()

    async def pending_request(player: dict[str, Any]) -> dict[str, Any]:
        mock_kodi.get_stream_info.side_effect = None
        requested.set()
        await replied.wait()
        return MagicMock(side_effect=[first_reply])()

    mock_kodi.get_stream_info.side_effect = pending_request
    mock_kodi.get_stream_info.return_value = stream_info("hlg")

    async with asyncio.timeout(5):
        await av_start(mock_connection)
        await requested.wait()
        await request_refresh(hass, mock_connection)
    # The cooldown ends while the first refresh still waits for Kodi
    freezer.tick(REQUEST_REFRESH_COOLDOWN * 2)
    async_fire_time_changed(hass)
    async with asyncio.timeout(5):
        await asyncio.sleep(0)
        replied.set()
        await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == first_state

    await advance_time(hass, freezer, REQUEST_REFRESH_COOLDOWN)

    assert hass.states.get(ENTITY_ID).state == "hlg"
    assert mock_kodi.get_stream_info.await_count == 3


@websocket
async def test_refresh_after_reconnect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the dynamic range is read again once the watchdog reconnects."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connect.side_effect = connect
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    await quit_kodi(mock_connection)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN
    mock_kodi.get_stream_info.return_value = stream_info("hlg")

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == "hlg"


@websocket
async def test_no_refresh_after_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the unload ends the subscriptions, so Kodi no longer refreshes."""
    subscribe = mock_connection.subscribe.side_effect
    unsubscribes: dict[str, MagicMock] = {}

    def track_subscribe(
        method: str, handler: Callable[[str, Any], Any]
    ) -> Callable[[], None]:
        unsubscribes[method] = MagicMock(side_effect=subscribe(method, handler))
        return unsubscribes[method]

    mock_connection.subscribe.side_effect = track_subscribe
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    await advance_time(hass, freezer, REQUEST_REFRESH_COOLDOWN)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await av_change(mock_connection)
    await hass.async_block_till_done()

    mock_kodi.get_stream_info.assert_awaited_once()
    assert {method: unsub.call_count for method, unsub in unsubscribes.items()} == {
        "Player.OnAVStart": 1,
        "Player.OnAVChange": 1,
        "Player.OnStop": 1,
        "System.OnQuit": 1,
        "System.OnRestart": 1,
        "System.OnSleep": 1,
    }


@websocket
async def test_no_poll_over_websocket(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the dynamic range is not polled over WebSocket."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    mock_kodi.get_stream_info.assert_awaited_once()


async def test_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the dynamic range is polled without WebSocket."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    # The coordinator can schedule the poll up to a second early
    await advance_time(hass, freezer, SCAN_INTERVAL - timedelta(seconds=2))
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN
    mock_kodi.get_players.assert_not_awaited()

    await advance_time(hass, freezer, timedelta(seconds=2))
    assert hass.states.get(ENTITY_ID).state == "hdr10"

    set_idle(mock_kodi)
    await advance_time(hass, freezer, SCAN_INTERVAL)
    assert hass.states.get(ENTITY_ID).state == "sdr"

    mock_kodi.get_players.side_effect = TransportError("Connection refused")
    await advance_time(hass, freezer, SCAN_INTERVAL)
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    "end_entry",
    [
        pytest.param(
            lambda hass, entry_id: hass.config_entries.async_unload(entry_id),
            id="unload",
        ),
        pytest.param(
            lambda hass, entry_id: hass.config_entries.async_reload(entry_id),
            id="reload",
        ),
    ],
)
async def test_no_stream_request_after_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    end_entry: Callable[[HomeAssistant, str], Awaitable[bool]],
) -> None:
    """Test a refresh that runs during the unload sends no further request."""
    await set_video(hass, mock_kodi, "hdr10")
    await setup_integration(hass, mock_config_entry)
    requested = asyncio.Event()
    replied = asyncio.Event()

    async def pending_request() -> list[dict[str, Any]]:
        mock_kodi.get_players.side_effect = None
        requested.set()
        await replied.wait()
        return [VIDEO_PLAYER]

    mock_kodi.get_players.side_effect = pending_request

    assert await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": ENTITY_ID}
    )
    async with asyncio.timeout(5):
        await requested.wait()
        assert await end_entry(hass, mock_config_entry.entry_id)
        replied.set()
        await hass.async_block_till_done()

    mock_kodi.get_stream_info.assert_not_awaited()
