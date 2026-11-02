"""Tests for setting up and unloading the Nemlig.com integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nemlig.api import (
    NemligAuthError,
    NemligClient,
    NemligConnectionError,
)

LOGIN = "custom_components.nemlig.api.NemligClient.async_login"


async def test_setup_and_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A working login loads the entry with a client; unloading works."""
    config_entry.add_to_hass(hass)
    with patch(LOGIN):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert isinstance(config_entry.runtime_data, NemligClient)

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
    with patch(LOGIN, side_effect=error):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is state
    reauth = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert bool(reauth) is (error is NemligAuthError)
