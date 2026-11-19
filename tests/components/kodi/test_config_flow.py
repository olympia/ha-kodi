"""Test the Kodi config flow."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, PropertyMock, patch

from pykodi.kodi import KodiWSConnection
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.kodi.config_flow import CannotConnectError, InvalidAuthError
from custom_components.kodi.const import CONF_WS_PORT, DEFAULT_TIMEOUT, DOMAIN
from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .util import (
    TEST_CREDENTIALS,
    TEST_DISCOVERY,
    TEST_DISCOVERY_WO_UUID,
    TEST_HOST,
    TEST_WS_PORT,
    UUID,
    MockConnection,
    MockWSConnection,
    get_kodi_connection,
)


@contextmanager
def _patch_kodi_success() -> Generator[None]:
    """Patch a reachable Kodi and the entry setup."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
        patch(
            "custom_components.kodi.async_setup_entry",
            return_value=None,
        ),
    ):
        yield


@pytest.fixture
async def user_flow(hass: HomeAssistant) -> str:
    """Return a user-initiated flow after filling in host info."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    return result["flow_id"]


async def test_user_flow(hass: HomeAssistant, user_flow: str) -> None:
    """Test a successful user initiated flow."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
        patch(
            "custom_components.kodi.async_setup_entry",
            return_value=None,
        ) as mock_setup_entry,
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_HOST["host"]
    assert result["result"].unique_id is None
    assert result["data"] == {
        **TEST_HOST,
        **TEST_WS_PORT,
        "password": None,
        "username": None,
        "name": None,
        "timeout": DEFAULT_TIMEOUT,
    }

    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_valid_auth(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle valid auth."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=InvalidAuthError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
        patch(
            "custom_components.kodi.async_setup_entry",
            return_value=None,
        ) as mock_setup_entry,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_CREDENTIALS
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None
    assert result["title"] == TEST_HOST["host"]
    assert result["data"] == {
        **TEST_HOST,
        **TEST_WS_PORT,
        **TEST_CREDENTIALS,
        "name": None,
        "timeout": DEFAULT_TIMEOUT,
    }

    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_valid_ws_port(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle valid websocket port."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection,
            "connect",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
        patch(
            "custom_components.kodi.async_setup_entry",
            return_value=None,
        ) as mock_setup_entry,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_WS_PORT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None
    assert result["title"] == TEST_HOST["host"]
    assert result["data"] == {
        **TEST_HOST,
        **TEST_WS_PORT,
        "password": None,
        "username": None,
        "name": None,
        "timeout": DEFAULT_TIMEOUT,
    }

    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_empty_ws_port(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle an empty websocket port input."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection,
            "connect",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {}

    with patch(
        "custom_components.kodi.async_setup_entry",
        return_value=None,
    ) as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"ws_port": 0}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None
    assert result["title"] == TEST_HOST["host"]
    assert result["data"] == {
        **TEST_HOST,
        "ws_port": None,
        "password": None,
        "username": None,
        "name": None,
        "timeout": DEFAULT_TIMEOUT,
    }

    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_invalid_auth(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle invalid auth."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=InvalidAuthError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=InvalidAuthError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_CREDENTIALS
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": "invalid_auth"}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=CannotConnectError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_CREDENTIALS
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": "cannot_connect"}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=Exception,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_CREDENTIALS
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": "unknown"}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection,
            "connect",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_CREDENTIALS
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_WS_PORT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None


async def test_form_cannot_connect_http(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle cannot connect over HTTP error."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=CannotConnectError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_HOST
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None


async def test_form_exception_http(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle generic exception over HTTP."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=Exception,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unknown"}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_HOST
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None


async def test_form_cannot_connect_ws(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle cannot connect over WebSocket error."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection,
            "connect",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection, "connected", new_callable=PropertyMock(return_value=False)
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_WS_PORT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {"base": "cannot_connect"}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=CannotConnectError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_WS_PORT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {"base": "cannot_connect"}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_WS_PORT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None


async def test_form_exception_ws(hass: HomeAssistant, user_flow: str) -> None:
    """Test we handle generic exception over WebSocket."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection,
            "connect",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {}

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(MockWSConnection, "connect", AsyncMock(side_effect=Exception)),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_WS_PORT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {"base": "unknown"}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_WS_PORT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None


async def test_discovery(hass: HomeAssistant) -> None:
    """Test discovery flow works."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    with patch(
        "custom_components.kodi.async_setup_entry",
        return_value=None,
    ) as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            flow_id=result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "hostname"
    assert result["data"] == {
        **TEST_HOST,
        **TEST_WS_PORT,
        "password": None,
        "username": None,
        "name": "hostname",
        "timeout": DEFAULT_TIMEOUT,
    }
    assert result["result"].unique_id == UUID

    assert len(mock_setup_entry.mock_calls) == 1


async def test_discovery_cannot_connect_http(hass: HomeAssistant) -> None:
    """Test discovery aborts if cannot connect."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=CannotConnectError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_discovery_cannot_connect_ws(hass: HomeAssistant) -> None:
    """Test discovery aborts if cannot connect to websocket."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection,
            "connect",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    assert result["errors"] == {}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_WS_PORT: 0}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_WS_PORT] is None
    assert result["result"].unique_id == UUID


async def test_discovery_exception_http(hass: HomeAssistant) -> None:
    """Test we handle generic exception during discovery validation."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=Exception,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unknown"


async def test_discovery_invalid_auth(hass: HomeAssistant) -> None:
    """Test we handle invalid auth during discovery."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=InvalidAuthError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_CREDENTIALS
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USERNAME] == TEST_CREDENTIALS["username"]
    assert result["result"].unique_id == UUID


async def test_discovery_duplicate_data(hass: HomeAssistant) -> None:
    """Test discovery aborts if same mDNS packet arrives."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=TEST_DISCOVERY
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"


async def test_discovery_updates_unique_id(hass: HomeAssistant) -> None:
    """Test a duplicate discovery id aborts and updates existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UUID,
        data={"host": "dummy", "port": 11, "namename": "dummy.local."},
    )

    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=TEST_DISCOVERY
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    assert entry.data["host"] == "1.1.1.1"
    assert entry.data["port"] == 8080
    assert entry.data["name"] == "hostname"


def _entry_at_address(
    host: str = "1.1.1.1",
    port: int = 8080,
    unique_id: str | None = None,
    disabled_by: config_entries.ConfigEntryDisabler | None = None,
) -> MockConfigEntry:
    """Return an entry for the Kodi at the given address."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        disabled_by=disabled_by,
        data={
            **TEST_HOST,
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_SSL: True,
            **TEST_WS_PORT,
            **TEST_CREDENTIALS,
            CONF_NAME: "Living room",
        },
    )


@pytest.mark.parametrize(
    "unique_id",
    [pytest.param(None, id="added_by_hand"), pytest.param(UUID, id="discovered")],
)
async def test_user_flow_already_configured(
    hass: HomeAssistant, user_flow: str, unique_id: str | None
) -> None:
    """Test the user flow aborts for a configured address without connecting."""
    _entry_at_address(port=8081).add_to_hass(hass)
    _entry_at_address(unique_id=unique_id).add_to_hass(hass)

    with patch(
        "custom_components.kodi.config_flow.get_kodi_connection"
    ) as mock_get_connection:
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_get_connection.assert_not_called()


@pytest.mark.parametrize(
    ("host", "port"),
    [
        pytest.param("1.1.1.1", 8081, id="other_port"),
        pytest.param("2.2.2.2", 8080, id="other_host"),
    ],
)
async def test_user_flow_other_address(
    hass: HomeAssistant, user_flow: str, host: str, port: int
) -> None:
    """Test the user flow adds a Kodi at an address that is not configured."""
    _entry_at_address(host=host, port=port).add_to_hass(hass)

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == 8080
    assert result["result"].unique_id is None


@pytest.mark.parametrize(
    "unique_id",
    [pytest.param(None, id="added_by_hand"), pytest.param(UUID, id="discovered")],
)
async def test_user_flow_address_configured_meanwhile(
    hass: HomeAssistant, user_flow: str, unique_id: str | None
) -> None:
    """Test the user flow aborts if the address got configured while it waited."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=InvalidAuthError,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)
    assert result["step_id"] == "credentials"

    _entry_at_address(unique_id=unique_id).add_to_hass(hass)
    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TEST_CREDENTIALS
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@contextmanager
def _patch_ws_unreachable() -> Generator[None]:
    """Let the HTTP check pass and the WebSocket check fail."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch.object(
            MockWSConnection,
            "connect",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            new=get_kodi_connection,
        ),
    ):
        yield


@pytest.mark.parametrize(
    "unique_id",
    [pytest.param(None, id="added_by_hand"), pytest.param(UUID, id="discovered")],
)
async def test_user_flow_at_ws_port_address_configured_meanwhile(
    hass: HomeAssistant, user_flow: str, unique_id: str | None
) -> None:
    """Test the user flow at the WebSocket port aborts for an address added meanwhile."""
    with _patch_ws_unreachable():
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)
    assert result["step_id"] == "ws_port"

    _entry_at_address(unique_id=unique_id).add_to_hass(hass)
    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_WS_PORT: 0}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_discovery_at_ws_port_entry_added_meanwhile(
    hass: HomeAssistant,
) -> None:
    """Test discovery at the WebSocket port aborts for a Kodi added by hand meanwhile."""
    with _patch_ws_unreachable():
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )
    assert result["step_id"] == "ws_port"

    entry = _entry_at_address()
    entry.add_to_hass(hass)
    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_WS_PORT: 0}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


@pytest.mark.parametrize(
    "disabled_by",
    [
        pytest.param(None, id="enabled"),
        pytest.param(config_entries.ConfigEntryDisabler.USER, id="disabled"),
    ],
)
async def test_discovery_aborts_for_entry_added_by_hand(
    hass: HomeAssistant, disabled_by: config_entries.ConfigEntryDisabler | None
) -> None:
    """Test discovery leaves an entry added by hand at its address alone."""
    _entry_at_address(host="2.2.2.2").add_to_hass(hass)
    entry = _entry_at_address(disabled_by=disabled_by)
    entry.add_to_hass(hass)
    data = dict(entry.data)

    with patch(
        "custom_components.kodi.config_flow.get_kodi_connection"
    ) as mock_get_connection:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.unique_id is None
    assert entry.data == data
    mock_get_connection.assert_not_called()


@pytest.mark.parametrize(
    ("host", "port", "unique_id"),
    [
        pytest.param("1.1.1.1", 8081, None, id="other_port"),
        pytest.param("2.2.2.2", 8080, None, id="other_host"),
        pytest.param(
            "1.1.1.1", 8080, "22222222-2222-2222-2222-222222222222", id="other_kodi"
        ),
    ],
)
async def test_discovery_next_to_other_entry(
    hass: HomeAssistant, host: str, port: int, unique_id: str | None
) -> None:
    """Test discovery goes on next to an entry of another address or another Kodi."""
    entry = _entry_at_address(host=host, port=port, unique_id=unique_id)
    entry.add_to_hass(hass)
    data = dict(entry.data)

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert entry.unique_id == unique_id

    with patch("custom_components.kodi.async_setup_entry", return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == UUID
    assert entry.unique_id == unique_id
    assert entry.data == data


async def test_discovery_confirm_after_entry_added_meanwhile(
    hass: HomeAssistant,
) -> None:
    """Test confirming a discovery aborts if the Kodi was added by hand meanwhile."""
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )
    assert result["step_id"] == "discovery_confirm"

    entry = _entry_at_address()
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.unique_id is None
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_discovery_next_to_ignored_kodi(hass: HomeAssistant) -> None:
    """Test discovery goes on next to an ignored discovery of another Kodi."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IGNORE},
        data={"unique_id": "22222222-2222-2222-2222-222222222222", "title": "Other"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "22222222-2222-2222-2222-222222222222"

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=TEST_DISCOVERY,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"


async def test_discovery_without_unique_id(hass: HomeAssistant) -> None:
    """Test a discovery flow with no unique id aborts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=TEST_DISCOVERY_WO_UUID,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_uuid"


REAUTH_ENTRY_DATA = {
    CONF_NAME: "name",
    CONF_HOST: "1.1.1.1",
    CONF_PORT: 8080,
    CONF_WS_PORT: 9090,
    CONF_USERNAME: "user",
    CONF_PASSWORD: "old_password",
    CONF_SSL: False,
}


async def test_reauth(hass: HomeAssistant) -> None:
    """Test reauth updates the credentials."""
    entry = MockConfigEntry(domain=DOMAIN, data=REAUTH_ENTRY_DATA, unique_id=UUID)
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with (
        _patch_kodi_success(),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ) as mock_get_connection,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "new_user", CONF_PASSWORD: "new_password"},
        )
        await hass.async_block_till_done()

    mock_get_connection.assert_called_once_with(
        "1.1.1.1", 8080, None, "new_user", "new_password", False, session=ANY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == REAUTH_ENTRY_DATA | {
        CONF_USERNAME: "new_user",
        CONF_PASSWORD: "new_password",
    }


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        pytest.param(InvalidAuthError, "invalid_auth", id="invalid_auth"),
        pytest.param(CannotConnectError, "cannot_connect", id="cannot_connect"),
        pytest.param(Exception, "unknown", id="unknown"),
    ],
)
async def test_reauth_errors(
    hass: HomeAssistant, side_effect: type[Exception], error: str
) -> None:
    """Test reauth handles errors and can recover."""
    entry = MockConfigEntry(domain=DOMAIN, data=REAUTH_ENTRY_DATA, unique_id=UUID)
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)

    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            side_effect=side_effect,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            return_value=MockConnection(),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "new_user", CONF_PASSWORD: "wrong_password"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": error}

    with _patch_kodi_success():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "new_user", CONF_PASSWORD: "new_password"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new_password"


def _ws_probe_factory(probe: MagicMock) -> Callable[..., Any]:
    """Return a connection factory that hands out the given WebSocket probe."""

    def factory(host: str, port: int, ws_port: int | None, *args: Any, **kwargs: Any):
        if ws_port is None:
            return MockConnection()
        return probe

    return factory


def _ws_probe(close_error: Exception | None = None) -> MagicMock:
    """Return a connected WebSocket probe."""
    probe = MagicMock(spec=KodiWSConnection)
    probe.connected = True
    probe.close.side_effect = close_error
    return probe


@pytest.mark.parametrize(
    "close_error",
    [pytest.param(None, id="closed"), pytest.param(TimeoutError(), id="close_error")],
)
async def test_ws_probe_closed_after_success(
    hass: HomeAssistant, user_flow: str, close_error: Exception | None
) -> None:
    """Test the WebSocket probe is closed after a successful check."""
    probe = _ws_probe(close_error)
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            side_effect=_ws_probe_factory(probe),
        ),
        patch(
            "custom_components.kodi.async_setup_entry",
            return_value=None,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None
    probe.close.assert_awaited_once_with()


@pytest.mark.parametrize(
    "close_error",
    [pytest.param(None, id="closed"), pytest.param(TimeoutError(), id="close_error")],
)
async def test_ws_probe_closed_after_failure(
    hass: HomeAssistant, user_flow: str, close_error: Exception | None
) -> None:
    """Test the WebSocket probe is closed when the check fails."""
    probe = _ws_probe(close_error)
    probe.connect.side_effect = CannotConnectError
    with (
        patch(
            "custom_components.kodi.config_flow.Kodi.ping",
            return_value=True,
        ),
        patch(
            "custom_components.kodi.config_flow.get_kodi_connection",
            side_effect=_ws_probe_factory(probe),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(user_flow, TEST_HOST)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ws_port"
    probe.close.assert_awaited_once_with()
