"""Test the Kodi config flow."""

from ipaddress import ip_address

from aiohttp import ContentTypeError, RequestInfo
from jsonrpc_base.jsonrpc import TransportError
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from custom_components.kodi.const import DEFAULT_SSL
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

TEST_HOST = {
    "host": "1.1.1.1",
    "port": 8080,
    "ssl": DEFAULT_SSL,
}

TEST_CREDENTIALS = {"username": "username", "password": "password"}


TEST_WS_PORT = {"ws_port": 9090}

UUID = "11111111-1111-1111-1111-111111111111"
TEST_DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address("1.1.1.1"),
    ip_addresses=[ip_address("1.1.1.1")],
    port=8080,
    hostname="hostname.local.",
    type="_xbmc-jsonrpc-h._tcp.local.",
    name="hostname._xbmc-jsonrpc-h._tcp.local.",
    properties={"uuid": UUID},
)


TEST_DISCOVERY_WO_UUID = ZeroconfServiceInfo(
    ip_address=ip_address("1.1.1.1"),
    ip_addresses=[ip_address("1.1.1.1")],
    port=8080,
    hostname="hostname.local.",
    type="_xbmc-jsonrpc-h._tcp.local.",
    name="hostname._xbmc-jsonrpc-h._tcp.local.",
    properties={},
)


TEST_IMPORT = {
    "name": "name",
    "host": "1.1.1.1",
    "port": 8080,
    "ws_port": 9090,
    "username": "username",
    "password": "password",
    "ssl": True,
    "timeout": 7,
}


def get_kodi_connection(
    host, port, ws_port, username, password, ssl=False, timeout=5, session=None
):
    """Get Kodi connection."""
    if ws_port is None:
        return MockConnection()
    return MockWSConnection()


class MockConnection:
    """A mock kodi connection."""

    def __init__(self, connected=True) -> None:
        """Mock the Kodi connection."""
        self._connected = connected

    async def connect(self):
        """Mock connect."""

    @property
    def connected(self):
        """Mock connected."""
        return self._connected

    @property
    def can_subscribe(self):
        """Mock can_subscribe."""
        return False

    async def close(self):
        """Mock close."""

    @property
    def server(self):
        """Mock server."""
        return None


class MockWSConnection:
    """A mock kodi websocket connection."""

    def __init__(self, connected=True) -> None:
        """Mock the websocket connection."""
        self._connected = connected

    async def connect(self):
        """Mock connect."""

    @property
    def connected(self):
        """Mock connected."""
        return self._connected

    @property
    def can_subscribe(self):
        """Mock can_subscribe."""
        return False

    async def close(self):
        """Mock close."""

    @property
    def server(self):
        """Mock server."""
        return None


def login_page_error() -> TransportError:
    """Return the error of a login page a proxy sends with 200 OK.

    It is wrapped like jsonrpc-async does, with the login in the request.
    """
    url = URL("http://1.1.1.1:8080/jsonrpc")
    request_info = RequestInfo(
        url,
        "POST",
        CIMultiDictProxy(CIMultiDict({"Authorization": "Basic dXNlcjpwYXNz"})),
        url,
    )
    return TransportError(
        "Cannot deserialize response body",
        None,
        ContentTypeError(
            request_info,
            (),
            status=200,
            message="Attempt to decode JSON with unexpected mimetype: text/html",
        ),
    )
