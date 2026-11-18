"""The Kodi integration."""

from jsonrpc_base.jsonrpc import ProtocolError
from pykodi import CannotConnectError, InvalidAuthError, Kodi, get_kodi_connection

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .connection import KodiConfigEntry, KodiConnectionManager
from .const import CONF_WS_PORT, DOMAIN
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.MEDIA_PLAYER]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the component."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KodiConfigEntry) -> None:
    """Set up Kodi from a config entry."""
    conn = get_kodi_connection(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_WS_PORT],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_SSL],
        session=async_get_clientsession(hass),
    )

    manager = KodiConnectionManager(hass, entry, conn, Kodi(conn))
    # Unload callbacks run in reverse order, so this one closes the connection
    # after the manager removed the timer of its watchdog. It also runs when
    # the setup fails or is cancelled.
    entry.async_on_unload(manager.async_stop)
    # Listen before the setup waits for Kodi, so stopping Home Assistant while
    # the setup reads the version closes the connection too. A stop during the
    # connect handshake does not, see PyKodi. A listener that removes itself
    # when it runs would log an error when the unload removes it again.
    entry.async_on_unload(
        hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, manager.async_stop)
    )

    await manager.async_setup()
    # Only a WebSocket connection is down here, when Kodi did not answer, and
    # a ping would then only add another timeout
    if conn.connected:
        await _async_check_credentials(hass, entry)

    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    manager.async_start()


async def _async_check_credentials(hass: HomeAssistant, entry: KodiConfigEntry) -> None:
    """Ping Kodi over HTTP, only its web server checks the credentials."""
    connection = get_kodi_connection(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        None,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_SSL],
        session=async_get_clientsession(hass),
    )
    try:
        await Kodi(connection).ping()
    except InvalidAuthError as error:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="login_failed",
            translation_placeholders={"host": entry.data[CONF_HOST]},
        ) from error
    except CannotConnectError, ProtocolError:
        # The credentials stay unchecked, as for a Kodi that is off
        pass


async def async_unload_entry(hass: HomeAssistant, entry: KodiConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
