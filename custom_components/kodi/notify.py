"""Kodi notification service."""

import logging
from typing import Any, override

import aiohttp
import jsonrpc_async
import probatio

from homeassistant.components.notify import (
    ATTR_DATA,
    ATTR_TITLE,
    ATTR_TITLE_DEFAULT,
    PLATFORM_SCHEMA as NOTIFY_PLATFORM_SCHEMA,
    BaseNotificationService,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PROXY_SSL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .connection import _error_text
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8080
DEFAULT_PROXY_SSL = False
DEFAULT_TIMEOUT = 5
# The JSON-RPC schema of GUI.ShowNotification rejects a shorter display time
MIN_DISPLAYTIME = 1500

PLATFORM_SCHEMA = NOTIFY_PLATFORM_SCHEMA.extend(
    {
        probatio.Required(CONF_HOST): cv.string,
        probatio.Optional(CONF_PORT, default=DEFAULT_PORT): probatio.Port(),
        probatio.Optional(CONF_PROXY_SSL, default=DEFAULT_PROXY_SSL): cv.boolean,
        probatio.Inclusive(CONF_USERNAME, "auth"): cv.string,
        probatio.Inclusive(probatio.Secret(CONF_PASSWORD), "auth"): cv.string,
    }
)

ATTR_DISPLAYTIME = "displaytime"
ATTR_ICON = "icon"


def _displaytime(value: Any) -> int:
    """Return the display time in milliseconds, or raise if Kodi would reject it."""
    try:
        displaytime = int(value)
    except TypeError, ValueError, OverflowError:
        displaytime = None
    if displaytime is None or displaytime < MIN_DISPLAYTIME:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_displaytime",
            translation_placeholders={"displaytime": str(value)},
        )
    return displaytime


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> KodiNotificationService:
    """Return the notify service."""
    username: str | None = config.get(CONF_USERNAME)
    password: str | None = config.get(CONF_PASSWORD)

    host: str = config[CONF_HOST]
    port: int = config[CONF_PORT]
    encryption = config.get(CONF_PROXY_SSL)

    if host.startswith(("http://", "https://")):
        host = host[host.index("://") + 3 :]
        _LOGGER.warning(
            "Kodi host name should no longer contain http:// See updated "
            "definitions here: "
            "https://www.home-assistant.io/integrations/kodi/"
        )

    http_protocol = "https" if encryption else "http"
    url = f"{http_protocol}://{host}:{port}/jsonrpc"

    if username is not None and password is not None:
        auth = aiohttp.BasicAuth(username, password, encoding="utf-8")
    else:
        auth = None

    return KodiNotificationService(hass, url, auth)


class KodiNotificationService(BaseNotificationService):
    """Implement the notification service for Kodi."""

    def __init__(self, hass, url, auth=None):
        """Initialize the service."""
        self._url = url

        kwargs = {"timeout": DEFAULT_TIMEOUT, "session": async_get_clientsession(hass)}

        if auth is not None:
            kwargs["auth"] = auth

        self._server = jsonrpc_async.Server(self._url, **kwargs)

    @override
    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        """Send a message to Kodi."""
        data = kwargs.get(ATTR_DATA) or {}

        displaytime = _displaytime(data.get(ATTR_DISPLAYTIME, 10000))
        icon = data.get(ATTR_ICON, "info")
        title = kwargs.get(ATTR_TITLE, ATTR_TITLE_DEFAULT)
        # A TransportError prints its arguments, and they can hold the request
        # with its login, so the errors are not chained
        try:
            await self._server.GUI.ShowNotification(title, message, icon, displaytime)
        except jsonrpc_async.TransportError as err:
            _LOGGER.debug(
                "Sending the notification to Kodi failed: %s", _error_text(err)
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="notify_failed",
            ) from None
        except jsonrpc_async.ProtocolError as err:
            _LOGGER.debug("Kodi rejected the notification: %s", _error_text(err))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="notify_rejected",
            ) from None
