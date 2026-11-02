"""Tests for the Nemlig.com actions and the basket sensor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nemlig.const import DOMAIN

from .conftest import load_fixture


def _basket_with(product_id: str, name: str, quantity: int) -> dict[str, Any]:
    basket = load_fixture("basket.json")
    basket["Lines"].append(
        {"Id": product_id, "Name": name, "Quantity": quantity, "Price": 10.0 * quantity}
    )
    basket["TotalProductsPrice"] += 10.0 * quantity
    return basket


async def _call(hass: HomeAssistant, service: str, data: dict, response: bool = True):
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=response
    )


async def test_basket_sensor(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The basket sensor shows the products total and the lines."""
    state = hass.states.get("sensor.nemlig_basket")
    assert state.state == "25.9"
    assert state.attributes["item_count"] == 2
    assert state.attributes["missing_to_minimum"] == 474.1
    assert state.attributes["lines"][0]["name"] == "Cocio kakaomælk"


async def test_add_product_by_id_adds_to_quantity(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
) -> None:
    """Adding a product already in the basket raises its quantity."""
    after = load_fixture("basket.json")
    after["Lines"][0]["Quantity"] = 3
    after["TotalProductsPrice"] = 38.85
    mock_api["set_basket_quantity"].return_value = after

    result = await _call(hass, "add_product", {"product_id": "701025"})

    mock_api["set_basket_quantity"].assert_awaited_once()
    assert mock_api["set_basket_quantity"].await_args.args == ("701025", 3)
    assert result["status"] == "added"
    assert result["quantity"] == 3
    assert hass.states.get("sensor.nemlig_basket").state == "38.85"


async def test_add_product_by_query(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
) -> None:
    """A query is resolved through the product index."""
    await hass.async_block_till_done()  # index refresh runs in the background
    mock_api["set_basket_quantity"].return_value = _basket_with(
        "5064654", "Black Coffee Blend Supreme", 2
    )

    result = await _call(hass, "add_product", {"query": "kaffe", "quantity": 2})

    assert mock_api["set_basket_quantity"].await_args.args == ("5064654", 2)
    assert result["name"] == "Black Coffee Blend Supreme"


async def test_add_product_ambiguous(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
) -> None:
    """An unclear query returns candidates, or raises without a response."""
    await hass.async_block_till_done()

    result = await _call(hass, "add_product", {"query": "mælk"})
    assert result["status"] == "ambiguous"
    assert {c["product_id"] for c in result["candidates"]} == {"701025", "5000123"}

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "add_product", {"query": "mælk"}, response=False)
    assert err.value.translation_key == "product_ambiguous"
    mock_api["set_basket_quantity"].assert_not_awaited()


async def test_add_product_not_found(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
) -> None:
    """A query matching nothing bought before returns search suggestions."""
    await hass.async_block_till_done()

    result = await _call(hass, "add_product", {"query": "gær"})

    assert result["status"] == "not_found"
    assert result["suggestions"][0]["product_id"] == "701025"
    mock_api["set_basket_quantity"].assert_not_awaited()


async def test_add_product_rejected(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
) -> None:
    """A product nemlig.com leaves out of the basket is an error."""
    mock_api["set_basket_quantity"].return_value = load_fixture("basket.json")
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "add_product", {"product_id": "999"})
    assert err.value.translation_key == "product_not_added"


async def test_add_shopping_list(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
) -> None:
    """A list is found by name, case-insensitively."""
    result = await _call(hass, "add_shopping_list", {"list": "weekend"})
    mock_api["add_shopping_list_to_basket"].assert_awaited_once()
    assert mock_api["add_shopping_list_to_basket"].await_args.args == (123457,)
    assert result["list"] == "Weekend"

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "add_shopping_list", {"list": "jul"})
    assert err.value.translation_key == "list_not_found"


async def test_search(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
) -> None:
    """Search returns compact product records."""
    result = await _call(hass, "search", {"query": "cocio", "limit": 3})
    assert result["products"][0] == {
        "product_id": "701025",
        "name": "Cocio kakaomælk",
        "brand": "Cocio",
        "description": None,
        "price": 12.95,
        "unit_price": None,
        "unit_price_label": None,
        "on_offer": False,
        "offer_price": None,
        "offer_min_quantity": None,
        "available": True,
    }
    assert mock_api["search_products"].await_args.kwargs == {"limit": 3}
