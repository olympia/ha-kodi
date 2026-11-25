"""Tests for the Kodi notify platform."""

import logging
import math
from typing import Any
from unittest.mock import AsyncMock, patch

import jsonrpc_async
import pytest

from custom_components.kodi.const import DOMAIN
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component

from .util import login_page_error

NOTIFY_CONFIG = {
    NOTIFY_DOMAIN: {
        "platform": "kodi",
        "name": "kodi",
        "host": "1.1.1.1",
        "port": 8080,
    }
}


async def setup_notify(hass: HomeAssistant, server: AsyncMock) -> None:
    """Set up the Kodi notify platform with a mocked server."""
    with patch("jsonrpc_async.Server", return_value=server):
        assert await async_setup_component(hass, NOTIFY_DOMAIN, NOTIFY_CONFIG)
        await hass.async_block_till_done()


async def test_send_message(hass: HomeAssistant) -> None:
    """Test sending a notification to Kodi."""
    server = AsyncMock()
    await setup_notify(hass, server)

    await hass.services.async_call(
        NOTIFY_DOMAIN, "kodi", {"message": "Hello", "title": "Test"}, blocking=True
    )

    server.GUI.ShowNotification.assert_called_once_with("Test", "Hello", "info", 10000)


@pytest.mark.parametrize(
    ("displaytime", "sent"),
    [(1500, 1500), ("5000", 5000), (2500.9, 2500)],
)
async def test_send_message_with_displaytime(
    hass: HomeAssistant, displaytime: Any, sent: int
) -> None:
    """Test the display time goes to Kodi as a whole number of milliseconds."""
    server = AsyncMock()
    await setup_notify(hass, server)

    await hass.services.async_call(
        NOTIFY_DOMAIN,
        "kodi",
        {"message": "Hello", "data": {"displaytime": displaytime, "icon": "warning"}},
        blocking=True,
    )

    server.GUI.ShowNotification.assert_called_once_with(
        "Home Assistant", "Hello", "warning", sent
    )
    assert type(server.GUI.ShowNotification.call_args.args[3]) is int


@pytest.mark.parametrize(
    "displaytime", [1499, "5", "abc", "5000.5", None, math.inf, [5000]]
)
async def test_send_message_with_invalid_displaytime(
    hass: HomeAssistant, displaytime: Any
) -> None:
    """Test a display time Kodi would reject is refused before sending."""
    server = AsyncMock()
    await setup_notify(hass, server)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            NOTIFY_DOMAIN,
            "kodi",
            {"message": "Hello", "data": {"displaytime": displaytime}},
            blocking=True,
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "invalid_displaytime"
    assert err.value.translation_placeholders == {"displaytime": str(displaytime)}
    server.GUI.ShowNotification.assert_not_called()


async def test_send_message_transport_error(hass: HomeAssistant) -> None:
    """Test an unreachable Kodi raises an error instead of only logging."""
    server = AsyncMock()
    server.GUI.ShowNotification.side_effect = jsonrpc_async.TransportError(
        "Unable to connect"
    )
    await setup_notify(hass, server)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            NOTIFY_DOMAIN, "kodi", {"message": "Hello"}, blocking=True
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "notify_failed"
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__


async def test_send_message_rejected(hass: HomeAssistant) -> None:
    """Test an error reply of Kodi raises an error with its reason."""
    server = AsyncMock()
    server.GUI.ShowNotification.side_effect = jsonrpc_async.ProtocolError(
        -32602, "Invalid params.", {}
    )
    await setup_notify(hass, server)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            NOTIFY_DOMAIN, "kodi", {"message": "Hello"}, blocking=True
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "notify_rejected"
    assert err.value.translation_placeholders == {"error": "Invalid params. (-32602)"}
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__


async def test_background_notify_error_keeps_the_credentials_out_of_the_log(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the logged error of a notification sent in the background leaves out the login."""
    caplog.set_level(logging.DEBUG)
    server = AsyncMock()
    server.GUI.ShowNotification.side_effect = login_page_error()
    await setup_notify(hass, server)

    await hass.services.async_call(NOTIFY_DOMAIN, "kodi", {"message": "Hello"})
    await hass.async_block_till_done()

    assert [
        str(record.exc_info[1])
        for record in caplog.records
        if record.name == "homeassistant.core" and record.exc_info
    ] == ["Unable to send the notification to Kodi. Is Kodi online?"]
    assert "dXNlcjpwYXNz" not in caplog.text
