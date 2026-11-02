"""Tests for the Nemlig.com config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nemlig.api import (
    NemligAuthError,
    NemligConnectionError,
    NemligResponseError,
)
from custom_components.nemlig.const import DOMAIN

from .conftest import PASSWORD, USERNAME


@pytest.fixture
def mock_login() -> Generator[AsyncMock]:
    """Patch the client's login."""
    with patch(
        "custom_components.nemlig.api.NemligClient.async_login", autospec=True
    ) as login:
        yield login


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep the created entry from setting up."""
    with patch(
        "custom_components.nemlig.async_setup_entry", return_value=True
    ) as setup:
        yield setup


async def test_user_flow(hass: HomeAssistant, mock_login: AsyncMock) -> None:
    """A working login creates an entry keyed by the lower-cased email."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: " Anders@Example.com ", CONF_PASSWORD: PASSWORD},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Anders@Example.com"
    assert result["data"] == {
        CONF_USERNAME: "Anders@Example.com",
        CONF_PASSWORD: PASSWORD,
    }
    assert result["result"].unique_id == USERNAME


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (NemligAuthError, "invalid_auth"),
        (NemligConnectionError, "cannot_connect"),
        (NemligResponseError("changed"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_login: AsyncMock, error: Exception, key: str
) -> None:
    """Login errors are shown on the form, and the flow can recover."""
    mock_login.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "forkert"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    mock_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant, mock_login: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The same account cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME.upper(), CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_login.assert_not_called()


async def test_reauth(
    hass: HomeAssistant, mock_login: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Reauth stores the new password after a failed attempt."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_login.side_effect = NemligAuthError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "stadig-forkert"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "ny-adgangskode"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "ny-adgangskode"
