"""Tests for setting up and unloading the Nemlig.com integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nemlig.api import NemligAuthError, NemligConnectionError
from custom_components.nemlig.coordinator import NemligData

LOGIN = "custom_components.nemlig.api.NemligClient.async_login"


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A working login loads the entry with its coordinator; unloading works."""
    config_entry = setup_integration
    assert config_entry.state is ConfigEntryState.LOADED
    assert isinstance(config_entry.runtime_data, NemligData)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (NemligAuthError, ConfigEntryState.SETUP_ERROR),
        (NemligConnectionError, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    error: type[Exception],
    state: ConfigEntryState,
) -> None:
    """Rejected credentials start reauth; network trouble retries later."""
    config_entry.add_to_hass(hass)
    with patch(LOGIN, side_effect=error):  # no network mocks: login fails first
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is state
    reauth = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert bool(reauth) is (error is NemligAuthError)


async def test_first_refresh_failure_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api
) -> None:
    """A failing first delivery update retries setup later."""
    mock_api["get_delivery_spot"].side_effect = NemligConnectionError
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
