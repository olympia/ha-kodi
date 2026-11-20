"""Tests for the Kodi media player."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import hashlib
import logging
from typing import Any
from unittest.mock import MagicMock, call

from aiohttp import ClientConnectionError
from freezegun.api import FrozenDateTimeFactory
from jsonrpc_base.jsonrpc import ProtocolError, TransportError
from pykodi import CannotConnectError
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_load_json_object_fixture,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion
from syrupy.filters import props

from custom_components.kodi.const import DOMAIN
from homeassistant.components.media_player import (
    ATTR_MEDIA_ALBUM_NAME,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_EPISODE,
    ATTR_MEDIA_POSITION,
    ATTR_MEDIA_POSITION_UPDATED_AT,
    ATTR_MEDIA_SEASON,
    ATTR_MEDIA_SERIES_TITLE,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    SCAN_INTERVAL,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PICTURE,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from . import AUDIO_PLAYER, VIDEO_PLAYER, set_idle, set_playing, setup_integration
from .util import UUID

ENTITY_ID = "media_player.name"
ATTR_DYNAMIC_RANGE = "dynamic_range"

WATCHDOG_INTERVAL = timedelta(seconds=10)

websocket = pytest.mark.parametrize(
    "can_subscribe", [pytest.param(True, id="websocket")]
)


def player_notification(speed: int) -> dict[str, Any]:
    """Return the data of a Kodi player notification."""
    return {
        "item": {"id": 1, "type": "movie"},
        "player": {"playerid": 1, "speed": speed},
    }


async def advance_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, interval: timedelta
) -> None:
    """Let the given time pass."""
    freezer.tick(interval)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def start_home_assistant(hass: HomeAssistant) -> None:
    """Announce that Home Assistant has started."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


def cannot_connect(cause: Exception) -> CannotConnectError:
    """Return a PyKodi connection error raised from the given cause."""
    error = CannotConnectError()
    error.__cause__ = cause
    return error


def info_records(caplog: pytest.LogCaptureFixture, text: str) -> int:
    """Return how many records at info level contain the text."""
    return sum(
        record.levelno == logging.INFO and text in record.getMessage()
        for record in caplog.records
    )


async def pass_watchdog_interval(hass: HomeAssistant) -> None:
    """Let the watchdog interval pass."""
    async_fire_time_changed(hass, dt_util.utcnow() + WATCHDOG_INTERVAL)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_connection", "mock_kodi")
@pytest.mark.parametrize(
    "unique_id",
    [pytest.param(UUID, id="unique_id"), pytest.param(None, id="no_unique_id")],
)
async def test_entity_and_device(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the registered entity and device."""
    await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)
    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert devices == snapshot(name="devices")


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("speed", "expected_state"),
    [
        pytest.param(0, MediaPlayerState.PAUSED, id="paused"),
        pytest.param(1, MediaPlayerState.PLAYING, id="playing"),
    ],
)
async def test_polled_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    speed: int,
    expected_state: MediaPlayerState,
) -> None:
    """Test the state derived from the polled playback speed."""
    await set_playing(hass, mock_kodi, speed=speed)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == expected_state


@pytest.mark.parametrize(
    ("connected", "get_players"),
    [
        pytest.param(False, {"return_value": [VIDEO_PLAYER]}, id="not_connected"),
        pytest.param(
            True,
            {"side_effect": TransportError("Connection lost")},
            id="request_failed",
        ),
        pytest.param(True, {"return_value": None}, id="players_unavailable"),
    ],
)
async def test_polled_state_turns_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    connected: bool,
    get_players: dict[str, Any],
) -> None:
    """Test losing Kodi while polling turns the state off."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    await advance_time(hass, freezer, SCAN_INTERVAL)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING

    mock_connection.connected = connected
    mock_kodi.get_players.configure_mock(**get_players)
    await advance_time(hass, freezer, SCAN_INTERVAL)

    state = hass.states.get(ENTITY_ID)
    assert state.state == MediaPlayerState.OFF
    assert ATTR_MEDIA_TITLE not in state.attributes


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.freeze_time("2026-01-01 12:00:00+00:00")
@pytest.mark.parametrize(
    ("players", "fixture", "properties"),
    [
        pytest.param([VIDEO_PLAYER], "movie.json", {}, id="movie"),
        pytest.param([VIDEO_PLAYER], "episode.json", {}, id="episode"),
        pytest.param([AUDIO_PLAYER], "song.json", {}, id="song"),
        pytest.param([VIDEO_PLAYER], "channel.json", {"live": True}, id="live_channel"),
        pytest.param(
            [VIDEO_PLAYER, AUDIO_PLAYER], "movie.json", {}, id="first_of_two_players"
        ),
    ],
)
async def test_state_attributes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    snapshot: SnapshotAssertion,
    players: list[dict[str, Any]],
    fixture: str,
    properties: dict[str, Any],
) -> None:
    """Test the state attributes for each kind of playing item."""
    item = await async_load_json_object_fixture(hass, fixture, DOMAIN)
    await set_playing(hass, mock_kodi, item, players, **properties)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    # The entity picture contains a random access token
    assert hass.states.get(ENTITY_ID) == snapshot(exclude=props(ATTR_ENTITY_PICTURE))


@pytest.mark.usefixtures("mock_connection", "mock_kodi")
async def test_idle_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the state and attributes while nothing is playing."""
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    assert hass.states.get(ENTITY_ID) == snapshot


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("item", "expected_title"),
    [
        pytest.param(
            {"title": "Title", "label": "Label", "file": "/movie.mkv"},
            "Title",
            id="title",
        ),
        pytest.param(
            {"title": "", "label": "Label", "file": "/movie.mkv"},
            "Label",
            id="label",
        ),
        pytest.param(
            {"title": "", "label": "", "file": "/movie.mkv"},
            "/movie.mkv",
            id="file",
        ),
    ],
)
async def test_media_title_fallback(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    item: dict[str, Any],
    expected_title: str,
) -> None:
    """Test the media title falls back to the label and then the file."""
    await set_playing(hass, mock_kodi, item)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_TITLE] == expected_title


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("players", "item_type", "expected_content_type"),
    [
        pytest.param([VIDEO_PLAYER], "episode", MediaType.TVSHOW, id="known_type"),
        pytest.param([VIDEO_PLAYER], "unknown", MediaType.VIDEO, id="unknown_video"),
        pytest.param([AUDIO_PLAYER], "unknown", MediaType.MUSIC, id="unknown_audio"),
        pytest.param([VIDEO_PLAYER], "channel", MediaType.VIDEO, id="tv_channel"),
        pytest.param([AUDIO_PLAYER], "channel", MediaType.MUSIC, id="radio_channel"),
        pytest.param(
            [VIDEO_PLAYER, AUDIO_PLAYER],
            "unknown",
            MediaType.VIDEO,
            id="first_of_two_players",
        ),
    ],
)
async def test_media_content_type(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    players: list[dict[str, Any]],
    item_type: str,
    expected_content_type: MediaType,
) -> None:
    """Test unknown and channel items use the type of the active player."""
    await set_playing(hass, mock_kodi, {"type": item_type, "label": "Label"}, players)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_CONTENT_TYPE] == expected_content_type


@pytest.mark.usefixtures("mock_connection")
async def test_media_position_updated_at(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the position timestamp only moves when the position changes."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)
    first_update = dt_util.utcnow()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_POSITION] == 4205
    assert state.attributes[ATTR_MEDIA_POSITION_UPDATED_AT] == first_update

    await advance_time(hass, freezer, SCAN_INTERVAL)
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_POSITION_UPDATED_AT] == first_update

    await set_playing(
        hass,
        mock_kodi,
        time={"hours": 1, "minutes": 11, "seconds": 0, "milliseconds": 71},
    )
    await advance_time(hass, freezer, SCAN_INTERVAL)
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_POSITION] == 4260
    assert state.attributes[ATTR_MEDIA_POSITION_UPDATED_AT] == dt_util.utcnow()


@pytest.mark.usefixtures("mock_connection")
async def test_media_position_updated_at_after_idle(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the position timestamp is set again after playback was stopped."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    await advance_time(hass, freezer, SCAN_INTERVAL)

    set_idle(mock_kodi)
    await advance_time(hass, freezer, SCAN_INTERVAL)
    state = hass.states.get(ENTITY_ID)
    assert state.state == MediaPlayerState.IDLE
    assert ATTR_MEDIA_POSITION_UPDATED_AT not in state.attributes

    await set_playing(hass, mock_kodi)
    await advance_time(hass, freezer, SCAN_INTERVAL)
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_POSITION] == 4205
    assert state.attributes[ATTR_MEDIA_POSITION_UPDATED_AT] == dt_util.utcnow()


IMAGE_URL = "http://user:pass@1.1.1.1:8080/image/image%3A%2F%2F"


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("player", "fixture", "url"),
    [
        pytest.param(
            VIDEO_PLAYER,
            "movie.json",
            f"{IMAGE_URL}https%253a%252f%252fimage.example.com%252fmovie-poster.jpg%2F",
            id="movie_poster",
        ),
        pytest.param(
            VIDEO_PLAYER,
            "episode.json",
            f"{IMAGE_URL}https%253a%252f%252fimage.example.com%252fepisode.jpg%2F",
            id="episode_thumbnail",
        ),
        pytest.param(
            AUDIO_PLAYER,
            "song.json",
            f"{IMAGE_URL}music%40%252fmedia%252fmusic%252fArtist%252fAlbum%252f01%2520-%2520Song%2520title.flac%2F",
            id="song_thumbnail",
        ),
        pytest.param(
            VIDEO_PLAYER,
            "channel.json",
            f"{IMAGE_URL}pvrchannel_tv%40http%253a%252f%252fpvr.example.com%252fimagecache%252f80%2F",
            id="channel_logo",
        ),
    ],
)
async def test_entity_picture_of_library_item(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    player: dict[str, Any],
    fixture: str,
    url: str,
) -> None:
    """Test the entity picture of the items Kodi was seen to send."""
    item = await async_load_json_object_fixture(hass, fixture, DOMAIN)
    await set_playing(hass, mock_kodi, item, [player])
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    picture = hass.states.get(ENTITY_ID).attributes[ATTR_ENTITY_PICTURE]
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    assert picture.endswith(f"&cache={url_hash}")


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("item", "url"),
    [
        pytest.param(
            {"label": "Movie", "thumbnail": "image://movie.jpg/"},
            f"{IMAGE_URL}movie.jpg%2F",
            id="thumbnail_without_art",
        ),
        pytest.param(
            {
                "label": "Movie",
                "thumbnail": "image://video@%2fmedia%2fmovies%2fMovie%20(2026)%2fMovie.2026.2160p.mkv/",
                "art": {
                    "poster": "image://https%3a%2f%2fimage.example.com%2fmovie-poster.jpg/",
                    "thumb": "image://video@%2fmedia%2fmovies%2fMovie%20(2026)%2fMovie.2026.2160p.mkv/",
                },
            },
            f"{IMAGE_URL}video%40%252fmedia%252fmovies%252fMovie%2520%282026%29%252fMovie.2026.2160p.mkv%2F",
            id="movie_extracted_thumbnail",
        ),
        pytest.param(
            {
                "label": "Episode",
                "thumbnail": "",
                "art": {
                    "season.poster": "image://https%3a%2f%2fimage.example.com%2fseason-poster.jpg/",
                    "tvshow.poster": "image://https%3a%2f%2fimage.example.com%2ftvshow-poster.jpg/",
                },
            },
            f"{IMAGE_URL}https%253a%252f%252fimage.example.com%252fseason-poster.jpg%2F",
            id="episode_season_poster",
        ),
        pytest.param(
            {
                "label": "Episode",
                "thumbnail": "",
                "art": {
                    "tvshow.poster": "image://https%3a%2f%2fimage.example.com%2ftvshow-poster.jpg/"
                },
            },
            f"{IMAGE_URL}https%253a%252f%252fimage.example.com%252ftvshow-poster.jpg%2F",
            id="episode_tvshow_poster",
        ),
        pytest.param(
            {
                "label": "Item",
                "thumbnail": "https://image.example.com/programme.webp",
                "art": {
                    "poster": "image://https%3a%2f%2fimage.example.com%2fmovie-poster.jpg/",
                    "thumb": "image://pvrchannel_tv@http%3a%2f%2fpvr.example.com%2fimagecache%2f80/",
                },
            },
            f"{IMAGE_URL}https%253a%252f%252fimage.example.com%252fmovie-poster.jpg%2F",
            id="poster_before_thumb",
        ),
        pytest.param(
            {
                "label": "Item",
                "thumbnail": "https://image.example.com/programme.webp",
                "art": {
                    "thumb": "image://pvrchannel_tv@http%3a%2f%2fpvr.example.com%2fimagecache%2f80/",
                    "season.poster": "image://https%3a%2f%2fimage.example.com%2fseason-poster.jpg/",
                },
            },
            f"{IMAGE_URL}pvrchannel_tv%40http%253a%252f%252fpvr.example.com%252fimagecache%252f80%2F",
            id="thumb_before_season_poster",
        ),
    ],
)
async def test_entity_picture(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    item: dict[str, Any],
    url: str,
) -> None:
    """Test the entity picture is the thumbnail, or the art Kodi has instead."""
    await set_playing(hass, mock_kodi, item)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    picture = hass.states.get(ENTITY_ID).attributes[ATTR_ENTITY_PICTURE]
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    assert picture.endswith(f"&cache={url_hash}")


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    "item",
    [
        pytest.param({"label": "Movie", "thumbnail": "", "art": {}}, id="no_art"),
        pytest.param(
            {
                "label": "Movie",
                "thumbnail": "",
                "art": {
                    "fanart": "image://https%3a%2f%2fimage.example.com%2ffanart.jpg/"
                },
            },
            id="only_fanart",
        ),
        pytest.param(
            {
                "label": "Item",
                "thumbnail": "https://image.example.com/programme.webp",
                "art": {},
            },
            id="web_thumbnail",
        ),
    ],
)
async def test_no_entity_picture(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    item: dict[str, Any],
) -> None:
    """Test an item without a usable image has no entity picture."""
    await set_playing(hass, mock_kodi, item)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    assert ATTR_ENTITY_PICTURE not in hass.states.get(ENTITY_ID).attributes


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("item", "expected_dynamic_range"),
    [
        pytest.param({"label": "Label"}, "sdr", id="no_streamdetails"),
        pytest.param(
            {"streamdetails": {"video": [{"hdrtype": ""}]}},
            "sdr",
            id="empty_hdrtype",
        ),
        pytest.param(
            {"streamdetails": {"video": [{"hdrtype": "hdr10"}]}},
            "hdr10",
            id="hdr10",
        ),
        pytest.param(
            {"streamdetails": {"video": [{"hdrtype": "hlg"}, {"hdrtype": "hdr10"}]}},
            "hlg",
            id="first_of_two_streams",
        ),
    ],
)
async def test_dynamic_range(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    item: dict[str, Any],
    expected_dynamic_range: str,
) -> None:
    """Test the dynamic range attribute follows the video stream details."""
    await set_playing(hass, mock_kodi, item)
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_DYNAMIC_RANGE] == expected_dynamic_range


@pytest.mark.usefixtures("mock_kodi")
async def test_no_dynamic_range_when_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the dynamic range attribute is not set while Kodi is off."""
    mock_connection.connected = False
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    state = hass.states.get(ENTITY_ID)
    assert state.state == MediaPlayerState.OFF
    assert ATTR_DYNAMIC_RANGE not in state.attributes


@websocket
async def test_websocket_setup(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test a connected websocket refreshes the state and the device."""
    await set_playing(hass, mock_kodi)

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, mock_config_entry.unique_id), mock_config_entry.entry_id
    )
    assert device.sw_version == "21.2"


@websocket
@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("speed", "expected_state"),
    [
        pytest.param(0, MediaPlayerState.PAUSED, id="paused"),
        pytest.param(1, MediaPlayerState.PLAYING, id="playing"),
    ],
)
async def test_websocket_setup_while_refresh_in_progress(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    speed: int,
    expected_state: MediaPlayerState,
) -> None:
    """Test adding the entity while Kodi is playing and the first refresh is pending."""
    await set_playing(hass, mock_kodi, speed=speed)
    get_player_properties = mock_kodi.get_player_properties.side_effect

    async def slow_get_player_properties(*args: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return get_player_properties(*args)

    async def slow_ping() -> bool:
        await asyncio.sleep(0)
        return True

    mock_kodi.get_player_properties.side_effect = slow_get_player_properties
    mock_kodi.ping.side_effect = slow_ping

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == expected_state


@websocket
@pytest.mark.usefixtures("mock_connection")
async def test_websocket_does_not_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the state is only refreshed by notifications over the websocket."""
    await setup_integration(hass, mock_config_entry)
    mock_kodi.get_players.reset_mock()

    await advance_time(hass, freezer, SCAN_INTERVAL)

    mock_kodi.get_players.assert_not_awaited()


@websocket
@pytest.mark.parametrize(
    ("notification", "speed", "expected_state"),
    [
        pytest.param("OnPlay", 1, MediaPlayerState.PLAYING, id="play"),
        pytest.param("OnPause", 0, MediaPlayerState.PAUSED, id="pause"),
        pytest.param("OnResume", 1, MediaPlayerState.PLAYING, id="resume"),
        pytest.param("OnAVStart", 1, MediaPlayerState.PLAYING, id="av_start"),
        pytest.param("OnAVChange", 1, MediaPlayerState.PLAYING, id="av_change"),
        pytest.param("OnSeek", 1, MediaPlayerState.PLAYING, id="seek"),
        pytest.param("OnSpeedChanged", 2, MediaPlayerState.PLAYING, id="speed_changed"),
    ],
)
async def test_websocket_player_notification(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    notification: str,
    speed: int,
    expected_state: MediaPlayerState,
) -> None:
    """Test player notifications refresh the state from Kodi."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.IDLE
    await set_playing(hass, mock_kodi, speed=speed)

    getattr(mock_connection.server.Player, notification)(
        "xbmc", player_notification(speed)
    )
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == expected_state
    assert state.attributes[ATTR_MEDIA_TITLE] == "Movie title"


@websocket
async def test_websocket_seek_refreshes_position(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test a seek in the playing library item refreshes the position."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_POSITION] == 4205

    await set_playing(
        hass,
        mock_kodi,
        time={"hours": 1, "minutes": 11, "seconds": 0, "milliseconds": 71},
    )
    mock_connection.server.Player.OnSeek("xbmc", player_notification(1))
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_POSITION] == 4260


@websocket
async def test_websocket_refresh_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test a failed refresh keeps the state reported by the notification."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    mock_kodi.get_players.side_effect = TransportError("Connection lost")

    mock_connection.server.Player.OnPause("xbmc", player_notification(0))
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PAUSED


async def stop_playback(connection: MagicMock) -> None:
    """Let Kodi report that the playback stopped."""
    connection.server.Player.OnStop(
        "xbmc", {"end": False, "item": {"id": 1, "type": "movie"}}
    )


async def quit_kodi(connection: MagicMock) -> None:
    """Let Kodi report that it quits."""
    await connection.server.System.OnQuit("xbmc", None)


REFRESH_REQUESTS = [
    "get_players",
    "get_application_properties",
    "get_player_properties",
    "get_playing_item_properties",
]


@websocket
@pytest.mark.parametrize("request_name", REFRESH_REQUESTS)
@pytest.mark.parametrize(
    ("notification", "expected_state"),
    [
        pytest.param(stop_playback, MediaPlayerState.IDLE, id="stop"),
        pytest.param(quit_kodi, MediaPlayerState.OFF, id="quit"),
    ],
)
async def test_websocket_reset_during_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    caplog: pytest.LogCaptureFixture,
    request_name: str,
    notification: Callable[[MagicMock], Awaitable[None]],
    expected_state: MediaPlayerState,
) -> None:
    """Test a stop or quit ends a refresh that waits for a reply."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING
    request = getattr(mock_kodi, request_name)
    reply = request.side_effect
    requested = asyncio.Event()
    replied = asyncio.Event()

    async def pending_request(*args: Any) -> Any:
        result = reply(*args) if reply else request.return_value
        requested.set()
        await replied.wait()
        return result

    request.side_effect = pending_request
    mock_kodi.reset_mock()

    async with asyncio.timeout(5):
        mock_connection.server.Player.OnPause("xbmc", player_notification(0))
        await requested.wait()
        await notification(mock_connection)
        replied.set()
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == expected_state
    assert ATTR_MEDIA_TITLE not in state.attributes
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    for later_request in REFRESH_REQUESTS[REFRESH_REQUESTS.index(request_name) + 1 :]:
        getattr(mock_kodi, later_request).assert_not_awaited()


@websocket
async def test_websocket_stop(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the stop notification sets the state to idle without a refresh."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING
    mock_kodi.get_players.reset_mock()

    mock_connection.server.Player.OnStop(
        "xbmc", {"end": False, "item": {"id": 1, "type": "movie"}}
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID) == snapshot
    mock_kodi.get_players.assert_not_awaited()


@websocket
async def test_websocket_volume_changed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
) -> None:
    """Test the volume notification updates the state without a refresh."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.8
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is False
    mock_kodi.get_players.reset_mock()

    mock_connection.server.Application.OnVolumeChanged(
        "xbmc", {"volume": 35, "muted": True}
    )
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.35
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is True
    mock_kodi.get_players.assert_not_awaited()


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_websocket_keypress(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
) -> None:
    """Test a key press notification is fired as an event."""
    await setup_integration(hass, mock_config_entry)
    events = async_capture_events(hass, f"{DOMAIN}_keypress")

    mock_connection.server.Other.OnKeyPress("KodiLivingroom", {"key": "volume_up"})
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "type": "keypress",
        "device_id": entity_registry.async_get(ENTITY_ID).device_id,
        "entity_id": ENTITY_ID,
        "sender": "KodiLivingroom",
        "data": {"key": "volume_up"},
    }


@websocket
@pytest.mark.parametrize("notification", ["OnQuit", "OnRestart", "OnSleep"])
async def test_websocket_system_notification(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    notification: str,
) -> None:
    """Test Kodi going away turns the state off and closes the connection."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING

    await getattr(mock_connection.server.System, notification)("xbmc", None)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    mock_connection.close.assert_awaited_once()

    mock_connection.server.Player.OnStop(
        "xbmc", {"end": False, "item": {"id": 1, "type": "movie"}}
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF


@websocket
@pytest.mark.parametrize(
    ("core_state", "trigger"),
    [
        pytest.param(
            CoreState.not_running, start_home_assistant, id="home_assistant_started"
        ),
        pytest.param(CoreState.running, pass_watchdog_interval, id="watchdog_interval"),
    ],
)
async def test_watchdog_connects(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    core_state: CoreState,
    trigger: Callable[[HomeAssistant], Awaitable[None]],
) -> None:
    """Test the watchdog connects the websocket and refreshes the state."""

    async def connect() -> None:
        mock_connection.connected = True

    hass.set_state(core_state)
    mock_connection.connected = False
    mock_connection.connect.side_effect = CannotConnectError
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    mock_connection.connect.side_effect = connect

    await trigger(hass)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING


@websocket
@pytest.mark.parametrize(
    "error", [CannotConnectError("Timeout"), TransportError("Connection lost")]
)
async def test_watchdog_ping_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Test a failed ping turns the state off and logs only once."""
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING
    mock_kodi.ping.side_effect = error

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    mock_connection.close.assert_awaited_once()

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 1


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_watchdog_connect_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a failed reconnect keeps the state off and logs only once."""
    mock_connection.connected = False
    mock_connection.connect.side_effect = CannotConnectError
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 1
    mock_connection.close.assert_not_awaited()


@websocket
async def test_watchdog_logs_again_after_ping_failure_and_reconnect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a ping failing again after a reconnect is reported again."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connect.side_effect = connect
    await setup_integration(hass, mock_config_entry)

    mock_kodi.ping.side_effect = TransportError("Connection lost")
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 1
    assert not mock_connection.connected

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)
    assert mock_connection.connected

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 2


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_watchdog_logs_again_after_reconnect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a connection lost after a reconnect is reported again."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connected = False
    mock_connection.connect.side_effect = CannotConnectError
    await setup_integration(hass, mock_config_entry)
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 1

    mock_connection.connect.side_effect = connect
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.IDLE

    mock_connection.connected = False
    mock_connection.connect.side_effect = CannotConnectError
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 2


@websocket
@pytest.mark.parametrize(
    "error", [TransportError("No reply"), ProtocolError("No version")]
)
async def test_watchdog_reconnect_without_version(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Test a reconnected Kodi that does not report its version stays off."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connect.side_effect = connect
    await setup_integration(hass, mock_config_entry)
    get_application_properties = mock_kodi.get_application_properties.side_effect

    mock_connection.connected = False
    mock_kodi.get_application_properties.side_effect = error
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 1

    mock_kodi.get_application_properties.side_effect = get_application_properties
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.IDLE
    assert info_records(caplog, "Kodi at 1.1.1.1 is available again") == 1


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_reload_keeps_version_while_kodi_is_off(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
) -> None:
    """Test the device keeps its version if Kodi is off when the entry loads."""
    await setup_integration(hass, mock_config_entry)
    mock_connection.connect.side_effect = CannotConnectError

    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, mock_config_entry.unique_id), mock_config_entry.entry_id
    )
    assert device.sw_version == "21.2"


@websocket
async def test_watchdog_reconnects_after_quit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the state returns once Kodi is back after it quit."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connect.side_effect = connect
    await set_playing(hass, mock_kodi)
    await setup_integration(hass, mock_config_entry)

    await mock_connection.server.System.OnQuit("xbmc", None)
    await mock_connection.server.System.OnQuit("xbmc", None)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    assert not mock_connection.connected

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING


@websocket
async def test_watchdog_reconnect_refreshes_state_and_version(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a reconnect after a silently lost connection refreshes the entity."""

    async def connect() -> None:
        mock_connection.connected = True

    async def get_application_properties(properties: list[str]) -> dict[str, Any]:
        return {"version": {"major": 22, "minor": 0}, "volume": 50, "muted": False}

    mock_connection.connect.side_effect = connect
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.IDLE

    mock_connection.connected = False
    await set_playing(hass, mock_kodi)
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING

    mock_connection.connected = False
    mock_kodi.get_application_properties.side_effect = get_application_properties
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, mock_config_entry.unique_id), mock_config_entry.entry_id
    )
    assert device.sw_version == "22.0"


@websocket
async def test_watchdog_recovers_from_failed_version_request(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the watchdog asks for the version again if Kodi did not answer."""
    get_application_properties = mock_kodi.get_application_properties.side_effect
    failed = False

    def fail_once(properties: list[str]) -> dict[str, Any]:
        nonlocal failed
        if not failed:
            failed = True
            raise TransportError("No reply")
        return get_application_properties(properties)

    mock_kodi.get_application_properties.side_effect = fail_once
    await set_playing(hass, mock_kodi)

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.PLAYING
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, mock_config_entry.unique_id), mock_config_entry.entry_id
    )
    assert device.sw_version == "21.2"
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 0


@websocket
@pytest.mark.parametrize(
    "error", [ProtocolError("No version"), TransportError("No reply")]
)
async def test_watchdog_closes_connection_without_version(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Test a Kodi that answers the ping but not the version request is off."""
    mock_kodi.get_application_properties.side_effect = error

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF
    mock_connection.close.assert_awaited_once()
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 1


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_watchdog_waits_for_slow_connect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the watchdog does not connect again while a connect is pending."""
    connected = asyncio.Event()

    async def connect() -> None:
        await connected.wait()
        mock_connection.connected = True

    await setup_integration(hass, mock_config_entry)
    mock_connection.connected = False
    mock_connection.connect.reset_mock()
    mock_connection.connect.side_effect = connect

    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    freezer.tick(WATCHDOG_INTERVAL)
    async_fire_time_changed(hass)
    connected.set()
    await hass.async_block_till_done()

    mock_connection.connect.assert_awaited_once()
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.IDLE


@websocket
async def test_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test unloading closes the connection and stops the watchdog."""
    await setup_integration(hass, mock_config_entry)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE
    mock_connection.close.assert_awaited_once()
    mock_connection.connect.reset_mock()

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)
    await mock_connection.server.System.OnQuit("xbmc", None)
    hass.set_state(CoreState.stopping)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    mock_connection.connect.assert_not_awaited()
    mock_connection.close.assert_awaited_once()


@websocket
async def test_removed_entity_ignores_kodi(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test an entity removed from a loaded entry no longer follows Kodi."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connect.side_effect = connect
    await setup_integration(hass, mock_config_entry)
    events = async_capture_events(hass, f"{DOMAIN}_keypress")
    entity_registry.async_remove(ENTITY_ID)
    await hass.async_block_till_done()
    mock_kodi.reset_mock()

    mock_connection.server.Player.OnPause("xbmc", player_notification(0))
    mock_connection.server.Other.OnKeyPress("xbmc", {"key": "volume_up"})
    await mock_connection.server.System.OnQuit("xbmc", None)
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert hass.states.get(ENTITY_ID) is None
    assert not events
    mock_kodi.get_players.assert_not_awaited()
    assert mock_connection.connected


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("season", "episode", "expected_season", "expected_episode"),
    [
        pytest.param(-1, -1, None, None, id="unset"),
        pytest.param(-2, -3, None, None, id="other_negative"),
        pytest.param(0, 0, 0, 0, id="special"),
    ],
)
async def test_season_and_episode(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    season: int,
    episode: int,
    expected_season: int | None,
    expected_episode: int | None,
) -> None:
    """Test negative season and episode numbers are not reported, while 0 is kept."""
    await set_playing(
        hass,
        mock_kodi,
        {"title": "Title", "season": season, "episode": episode},
    )
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    attributes = hass.states.get(ENTITY_ID).attributes
    assert attributes.get(ATTR_MEDIA_SEASON) == expected_season
    assert attributes.get(ATTR_MEDIA_EPISODE) == expected_episode


@pytest.mark.usefixtures("mock_connection")
async def test_empty_series_title_and_album(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test empty series title and album name are not reported."""
    await set_playing(hass, mock_kodi, {"title": "Title", "showtitle": "", "album": ""})
    await setup_integration(hass, mock_config_entry)

    await advance_time(hass, freezer, SCAN_INTERVAL)

    attributes = hass.states.get(ENTITY_ID).attributes
    assert ATTR_MEDIA_SERIES_TITLE not in attributes
    assert ATTR_MEDIA_ALBUM_NAME not in attributes


@websocket
@pytest.mark.usefixtures("mock_kodi")
async def test_watchdog_logs_available_again_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the end of an outage is logged once, at info level."""

    async def connect() -> None:
        mock_connection.connected = True

    mock_connection.connected = False
    mock_connection.connect.side_effect = CannotConnectError
    await setup_integration(hass, mock_config_entry)
    assert info_records(caplog, "Kodi at 1.1.1.1 is available again") == 0
    mock_connection.connect.side_effect = connect

    await advance_time(hass, freezer, WATCHDOG_INTERVAL)
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)

    assert info_records(caplog, "Kodi at 1.1.1.1 is available again") == 1
    assert not [
        record
        for record in caplog.records
        if DOMAIN in record.name.split(".") and record.levelno > logging.INFO
    ]


@pytest.mark.usefixtures("mock_connection")
async def test_poll_logs_unavailable_and_available_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test failed polls of an entry without websocket are logged once per outage."""
    await setup_integration(hass, mock_config_entry)
    mock_kodi.get_players.side_effect = TransportError("Connection refused")

    await advance_time(hass, freezer, SCAN_INTERVAL)
    await advance_time(hass, freezer, SCAN_INTERVAL)
    assert (
        info_records(caplog, "Kodi at 1.1.1.1 is unavailable: Connection refused") == 1
    )
    assert info_records(caplog, "Kodi at 1.1.1.1 is available again") == 0

    mock_kodi.get_players.side_effect = None
    await advance_time(hass, freezer, SCAN_INTERVAL)
    await advance_time(hass, freezer, SCAN_INTERVAL)

    assert info_records(caplog, "Kodi at 1.1.1.1 is available again") == 1
    assert not [
        record
        for record in caplog.records
        if DOMAIN in record.name.split(".") and record.levelno > logging.INFO
    ]


@websocket
@pytest.mark.parametrize(
    ("error", "text"),
    [
        pytest.param(
            cannot_connect(
                TransportError(
                    "Transport Error",
                    None,
                    ClientConnectionError("Connect call failed"),
                )
            ),
            "Kodi at 1.1.1.1 is unavailable: Connect call failed",
            id="cause",
        ),
        pytest.param(
            cannot_connect(TimeoutError()),
            "Kodi at 1.1.1.1 is unavailable: TimeoutError",
            id="timeout",
        ),
        pytest.param(
            TransportError("Transport Error", None, TimeoutError()),
            "Kodi at 1.1.1.1 is unavailable: TimeoutError",
            id="transport_timeout",
        ),
        pytest.param(
            TransportError("Connection lost"),
            "Kodi at 1.1.1.1 is unavailable: Connection lost",
            id="transport_error",
        ),
        pytest.param(
            TransportError(
                "Cannot deserialize response body", None, ValueError("Expecting value")
            ),
            "Kodi at 1.1.1.1 is unavailable: Cannot deserialize response body",
            id="invalid_response",
        ),
        pytest.param(
            TransportError.__new__(TransportError),
            "Kodi at 1.1.1.1 is unavailable: TransportError",
            id="transport_error_without_args",
        ),
        pytest.param(
            ProtocolError(
                -32100, "Failed to execute method.", {"id": "1", "error": {}}
            ),
            "Kodi at 1.1.1.1 is unavailable: Failed to execute method. (-32100)",
            id="error_response",
        ),
        pytest.param(
            CannotConnectError(),
            "Kodi at 1.1.1.1 is unavailable: CannotConnectError",
            id="no_text",
        ),
    ],
)
@pytest.mark.usefixtures("mock_kodi")
async def test_unavailable_log_names_the_cause(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    text: str,
) -> None:
    """Test the unavailable log shows the error behind the PyKodi exception."""
    mock_connection.connected = False
    mock_connection.connect.side_effect = error

    await setup_integration(hass, mock_config_entry)

    assert [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO and "is unavailable" in record.getMessage()
    ] == [text]


@websocket
async def test_websocket_update_does_not_log_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a refresh while the watchdog reads no version keeps the outage open."""

    async def connect() -> None:
        mock_connection.connected = True

    await async_setup_component(hass, "homeassistant", {})
    mock_connection.connect.side_effect = connect
    await setup_integration(hass, mock_config_entry)
    mock_connection.connected = False
    mock_kodi.get_application_properties.side_effect = ProtocolError(-32100, "Failed")
    await advance_time(hass, freezer, WATCHDOG_INTERVAL)
    assert mock_connection.connected

    mock_kodi.get_players.reset_mock()
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": ENTITY_ID}, blocking=True
    )

    mock_kodi.get_players.assert_awaited()
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 1
    assert info_records(caplog, "is available again") == 0


@websocket
async def test_websocket_update_error_does_not_log_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_connection: MagicMock,
    mock_kodi: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a failed refresh over websocket leaves the outage to the watchdog."""

    async def connect() -> None:
        mock_connection.connected = True

    await async_setup_component(hass, "homeassistant", {})
    mock_connection.connect.side_effect = connect
    await setup_integration(hass, mock_config_entry)
    mock_kodi.get_players.reset_mock()
    mock_kodi.get_players.side_effect = TransportError("No reply")

    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": ENTITY_ID}, blocking=True
    )

    mock_kodi.get_players.assert_awaited()
    assert info_records(caplog, "Kodi at 1.1.1.1 is unavailable") == 0


def _artist(artist_id: int, name: str) -> dict[str, Any]:
    return {"artist": name, "artistid": artist_id, "label": name}


def _album(album_id: int, title: str, artist_id: int) -> dict[str, Any]:
    return {"albumid": album_id, "artistid": [artist_id], "label": title}


def _song(song_id: int, title: str, artist_id: int) -> dict[str, Any]:
    return {"artistid": [artist_id], "label": title, "songid": song_id}


LIBRARY = {
    "artists": [
        _artist(1, "MrX Lamp"),
        _artist(2, "Mr. Lamp"),
        _artist(3, "[Missing Tag]"),
        _artist(4, "The Lanterns"),
    ],
    "albums": [
        _album(10, "Harbour Lights", 4),
        _album(11, "Night Drive", 4),
        _album(12, "Night Drive", 2),
        _album(13, "Unknown", 3),
    ],
    "songs": [
        _song(20, "Night Drive", 4),
        _song(21, "Night Drive (Live)", 4),
        _song(22, "Morning Light", 2),
        _song(23, "Night Drive", 2),
        _song(24, "Lamp Post", 2),
        _song(25, "Lamp\u00a0Light", 2),
    ],
}
EMPTY_LIBRARY: dict[str, list[dict[str, Any]]] = {
    "artists": [],
    "albums": [],
    "songs": [],
}


def set_library(mock_kodi: MagicMock, library: dict[str, list[dict[str, Any]]]) -> None:
    """Answer the library queries of the client like Kodi does."""

    def result(key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        limits = {"limits": {"end": len(items), "start": 0, "total": len(items)}}
        return {key: items, **limits} if items else limits

    def by_artist(key: str, artist_id: int | None) -> dict[str, Any]:
        # Without properties Kodi only returns the ID and the label
        return result(
            key,
            [
                {field: value for field, value in item.items() if field != "artistid"}
                for item in library[key]
                if not artist_id or artist_id in item["artistid"]
            ],
        )

    mock_kodi.get_artists.side_effect = lambda properties=None: result(
        "artists", library["artists"]
    )
    mock_kodi.get_albums.side_effect = (
        lambda artist_id=None, album_id=None, properties=None: by_artist(
            "albums", artist_id
        )
    )
    mock_kodi.get_songs.side_effect = (
        lambda artist_id=None, album_id=None, properties=None: by_artist(
            "songs", artist_id
        )
    )


def playlist_writes(mock_kodi: MagicMock) -> list[Any]:
    """Return the calls of the client that add to the playlist."""
    return [
        client_call
        for client_call in mock_kodi.mock_calls
        if client_call[0].startswith("add_")
    ]


async def add_to_playlist(hass: HomeAssistant, **data: str) -> None:
    """Call the add_to_playlist action on the Kodi media player."""
    await hass.services.async_call(
        DOMAIN,
        "add_to_playlist",
        {ATTR_ENTITY_ID: ENTITY_ID, **data},
        blocking=True,
    )


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("data", "method", "item_id"),
    [
        pytest.param(
            {"media_type": "SONG", "media_id": "22"},
            "add_song_to_playlist",
            22,
            id="song_id",
        ),
        pytest.param(
            {"media_type": "SONG", "media_id": "22", "media_name": "Night Drive"},
            "add_song_to_playlist",
            22,
            id="song_id_before_name",
        ),
        pytest.param(
            {"media_type": "ALBUM", "media_id": "12"},
            "add_album_to_playlist",
            12,
            id="album_id",
        ),
        pytest.param(
            {"media_type": "ALBUM", "media_id": "12", "media_name": "Harbour Lights"},
            "add_album_to_playlist",
            12,
            id="album_id_before_name",
        ),
        pytest.param(
            {"media_type": "SONG", "media_name": "night drive"},
            "add_song_to_playlist",
            20,
            id="first_best_song",
        ),
        pytest.param(
            {"media_type": "SONG", "media_name": "Night Drive (Live)"},
            "add_song_to_playlist",
            21,
            id="song_name_with_parentheses",
        ),
        pytest.param(
            {"media_type": "SONG", "media_name": "Lamp\u00a0Light"},
            "add_song_to_playlist",
            25,
            id="song_name_with_no_break_space",
        ),
        pytest.param(
            {
                "media_type": "SONG",
                "media_name": "Night Drive",
                "artist_name": "Mr. Lamp",
            },
            "add_song_to_playlist",
            23,
            id="song_of_artist_with_dot",
        ),
        pytest.param(
            {"media_type": "SONG", "media_name": "morning  drive "},
            "add_song_to_playlist",
            20,
            id="partial_song_name",
        ),
        pytest.param(
            {
                "media_type": "ALBUM",
                "media_name": "Night Drive",
                "artist_name": "Mr. Lamp",
            },
            "add_album_to_playlist",
            12,
            id="album_of_artist",
        ),
        pytest.param(
            {"media_type": "ALBUM", "media_name": "harbour"},
            "add_album_to_playlist",
            10,
            id="partial_album_name",
        ),
    ],
)
async def test_add_to_playlist(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    data: dict[str, str],
    method: str,
    item_id: int,
) -> None:
    """Test adding a song or an album to the playlist by ID or by name."""
    set_library(mock_kodi, LIBRARY)
    await setup_integration(hass, mock_config_entry)

    await add_to_playlist(hass, **data)

    assert playlist_writes(mock_kodi) == [getattr(call, method)(item_id)]


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("data", "album_ids"),
    [
        pytest.param({"artist_name": "The Lanterns"}, [10, 11], id="no_media_name"),
        pytest.param(
            {"media_name": "", "artist_name": "The Lanterns"},
            [10, 11],
            id="empty_media_name",
        ),
        pytest.param(
            {"media_name": "ALL", "artist_name": "the lanterns"}, [10, 11], id="all"
        ),
        pytest.param({"artist_name": "[Missing Tag]"}, [13], id="artist_with_brackets"),
    ],
)
async def test_add_all_albums_to_playlist(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    data: dict[str, str],
    album_ids: list[int],
) -> None:
    """Test adding all albums of an artist to the playlist."""
    set_library(mock_kodi, LIBRARY)
    await setup_integration(hass, mock_config_entry)

    await add_to_playlist(hass, media_type="ALBUM", **data)

    assert playlist_writes(mock_kodi) == [
        call.add_album_to_playlist(album_id) for album_id in album_ids
    ]


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    ("library", "data", "translation_key", "placeholders"),
    [
        pytest.param(
            LIBRARY,
            {"media_type": "song", "media_id": "22"},
            "invalid_media_type",
            {"media_type": "song"},
            id="media_type",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "SONG", "media_id": "22a"},
            "invalid_media_id",
            {"media_id": "22a"},
            id="song_id",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "SONG", "media_id": ""},
            "invalid_media_id",
            {"media_id": ""},
            id="empty_song_id",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "ALBUM", "media_id": "twelve"},
            "invalid_media_id",
            {"media_id": "twelve"},
            id="album_id",
        ),
        pytest.param(
            LIBRARY, {"media_type": "SONG"}, "missing_media", None, id="no_song"
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "SONG", "media_name": ""},
            "missing_media",
            None,
            id="empty_song_name",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "ALBUM"},
            "missing_artist_name",
            None,
            id="all_albums_without_artist",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "ALBUM", "media_name": ""},
            "missing_artist_name",
            None,
            id="empty_album_name_without_artist",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "SONG", "media_name": "Thunder Road"},
            "song_not_found",
            {"media_name": "Thunder Road"},
            id="song",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "SONG", "media_name": "Driv"},
            "song_not_found",
            {"media_name": "Driv"},
            id="part_of_word",
        ),
        pytest.param(
            LIBRARY,
            {
                "media_type": "SONG",
                "media_name": "Morning Light",
                "artist_name": "The Lanterns",
            },
            "song_not_found",
            {"media_name": "Morning Light"},
            id="song_of_other_artist",
        ),
        pytest.param(
            EMPTY_LIBRARY,
            {"media_type": "SONG", "media_name": "Night Drive"},
            "song_not_found",
            {"media_name": "Night Drive"},
            id="song_in_empty_library",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "ALBUM", "media_name": "Thunder Road"},
            "album_not_found",
            {"media_name": "Thunder Road"},
            id="album",
        ),
        pytest.param(
            EMPTY_LIBRARY,
            {"media_type": "ALBUM", "media_name": "Night Drive"},
            "album_not_found",
            {"media_name": "Night Drive"},
            id="album_in_empty_library",
        ),
        pytest.param(
            LIBRARY,
            {
                "media_type": "SONG",
                "media_name": "Night Drive",
                "artist_name": "Mister",
            },
            "artist_not_found",
            {"artist_name": "Mister"},
            id="artist_of_song",
        ),
        pytest.param(
            LIBRARY,
            {
                "media_type": "ALBUM",
                "media_name": "Night Drive",
                "artist_name": "Mister",
            },
            "artist_not_found",
            {"artist_name": "Mister"},
            id="artist_of_album",
        ),
        pytest.param(
            EMPTY_LIBRARY,
            {"media_type": "ALBUM", "artist_name": "The Lanterns"},
            "artist_not_found",
            {"artist_name": "The Lanterns"},
            id="artist_in_empty_library",
        ),
        pytest.param(
            LIBRARY,
            {"media_type": "ALBUM", "media_name": "ALL", "artist_name": "MrX Lamp"},
            "no_albums",
            {"artist_name": "MrX Lamp"},
            id="artist_without_albums",
        ),
    ],
)
async def test_add_to_playlist_invalid_input(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    library: dict[str, list[dict[str, Any]]],
    data: dict[str, str],
    translation_key: str,
    placeholders: dict[str, str] | None,
) -> None:
    """Test invalid input or media missing from the library adds nothing."""
    set_library(mock_kodi, library)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as exc_info:
        await add_to_playlist(hass, **data)

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == translation_key
    assert exc_info.value.translation_placeholders == placeholders
    assert playlist_writes(mock_kodi) == []


@pytest.mark.usefixtures("mock_connection")
@pytest.mark.parametrize(
    "error", [TransportError("No reply"), ProtocolError("Invalid params")]
)
async def test_add_to_playlist_library_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_kodi: MagicMock,
    error: Exception,
) -> None:
    """Test an error of the Kodi library is not reported as invalid input."""
    set_library(mock_kodi, LIBRARY)
    await setup_integration(hass, mock_config_entry)
    mock_kodi.get_songs.side_effect = error

    with pytest.raises(type(error)) as exc_info:
        await add_to_playlist(hass, media_type="SONG", media_name="Night Drive")

    assert exc_info.value is error
    assert playlist_writes(mock_kodi) == []
