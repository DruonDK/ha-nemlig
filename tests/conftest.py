"""Shared fixtures for the Nemlig.com tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nemlig.api import NemligClient
from custom_components.nemlig.const import DOMAIN

BASE_URL = "https://www.nemlig.com"
SEARCH_URL = "https://webapi.prod.knl.nemlig.it/searchgateway/api/search"
USERNAME = "anders@example.com"
PASSWORD = "hemmelig"


def load_fixture(name: str) -> Any:
    """Load a JSON fixture from tests/fixtures."""
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def mock_login(
    aioclient_mock: AiohttpClientMocker,
    *,
    login_status: int = 200,
    login_json: Any = None,
) -> None:
    """Register the requests of the 3-step login flow."""
    aioclient_mock.get(
        f"{BASE_URL}/webapi/AntiForgery", json=load_fixture("anti_forgery.json")
    )
    aioclient_mock.get(f"{BASE_URL}/webapi/Token", json=load_fixture("token.json"))
    aioclient_mock.post(
        f"{BASE_URL}/webapi/login",
        status=login_status,
        json=load_fixture("login.json") if login_json is None else login_json,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/nemlig."""


@pytest.fixture
async def client(aioclient_mock: AiohttpClientMocker) -> AsyncGenerator[NemligClient]:
    """A client on a mocked session, without throttling."""
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    yield NemligClient(session, USERNAME, PASSWORD, min_interval=0)
    await session.close()


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured nemlig.com account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=USERNAME,
        data={"username": USERNAME, "password": PASSWORD},
    )


@pytest.fixture
def mock_api() -> Generator[dict[str, AsyncMock]]:
    """Patch the client's network methods with fixture data."""
    methods = {
        "async_login": AsyncMock(),
        "get_delivery_spot": AsyncMock(return_value=load_fixture("delivery_spot.json")),
        "get_order_history": AsyncMock(return_value=load_fixture("order_history.json")),
    }
    with patch.multiple("custom_components.nemlig.api.NemligClient", **methods):
        yield methods


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: dict[str, AsyncMock]
) -> MockConfigEntry:
    """Set up the integration in Danish time with the fixture delivery."""
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
