"""Actions for the Nemlig.com integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import NemligError
from .const import DOMAIN
from .coordinator import NemligData
from .models import Basket

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_PRODUCT_ID = "product_id"
ATTR_QUERY = "query"
ATTR_QUANTITY = "quantity"
ATTR_LIST = "list"
ATTR_LIMIT = "limit"

SERVICE_ADD_PRODUCT = "add_product"
SERVICE_ADD_SHOPPING_LIST = "add_shopping_list"
SERVICE_SEARCH = "search"

ADD_PRODUCT_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
            vol.Exclusive(ATTR_PRODUCT_ID, "product"): cv.string,
            vol.Exclusive(ATTR_QUERY, "product"): cv.string,
            vol.Optional(ATTR_QUANTITY, default=1): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=50)
            ),
        }
    ),
    cv.has_at_least_one_key(ATTR_PRODUCT_ID, ATTR_QUERY),
)
ADD_SHOPPING_LIST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_LIST): cv.string,
    }
)
SEARCH_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_QUERY): cv.string,
        vol.Optional(ATTR_LIMIT, default=10): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=50)
        ),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_PRODUCT,
        _add_product,
        schema=ADD_PRODUCT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_SHOPPING_LIST,
        _add_shopping_list,
        schema=ADD_SHOPPING_LIST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        _search,
        schema=SEARCH_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _data(call: ServiceCall) -> NemligData:
    """The loaded entry the call targets; the only one when none is given."""
    entries = [
        entry
        for entry in call.hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    if len(entries) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_entry" if not entries else "entry_required",
        )
    return entries[0].runtime_data


def _api_error(err: NemligError) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="api_error",
        translation_placeholders={"error": str(err)},
    )


def _product(raw: dict[str, Any]) -> dict[str, Any]:
    """A search result in action-response form."""
    campaign = raw.get("Campaign") or {}
    availability = raw.get("Availability") or {}
    return {
        "product_id": str(raw.get("Id")),
        "name": raw.get("Name"),
        "brand": raw.get("Brand"),
        "description": raw.get("Description"),
        "price": raw.get("Price"),
        "unit_price": raw.get("UnitPriceCalc"),
        "unit_price_label": raw.get("UnitPriceLabel"),
        "on_offer": bool(raw.get("DiscountItem") or campaign),
        "offer_price": campaign.get("CampaignPrice") if campaign else None,
        "offer_min_quantity": campaign.get("MinQuantity") if campaign else None,
        "available": availability.get("IsAvailableInStock", True),
    }


def _basket_summary(basket: Basket) -> dict[str, Any]:
    return {
        "basket_total": basket.products_total,
        "basket_items": basket.item_count,
        "missing_to_minimum": basket.missing_to_minimum,
    }


async def _add_product(call: ServiceCall) -> ServiceResponse:
    """Add a product to the live basket, by id or by what the household usually buys."""
    data = _data(call)
    quantity: int = call.data[ATTR_QUANTITY]
    product_id: str | None = call.data.get(ATTR_PRODUCT_ID)

    if product_id is None:
        query: str = call.data[ATTR_QUERY]
        product, candidates = data.products.resolve(query)
        if product is None:
            if candidates:
                result: dict[str, Any] = {
                    "status": "ambiguous",
                    "candidates": [p.as_dict() for p in candidates],
                }
                key = "product_ambiguous"
                options = "; ".join(f"{p.name} [{p.product_id}]" for p in candidates)
            else:
                try:
                    found = await data.client.search_products(query, limit=5)
                except NemligError as err:
                    raise _api_error(err) from err
                result = {
                    "status": "not_found",
                    "suggestions": [_product(p) for p in found],
                }
                key = "product_not_found"
                options = "; ".join(f"{p.get('Name')} [{p.get('Id')}]" for p in found)
            if call.return_response:
                return result
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=key,
                translation_placeholders={"query": query, "options": options or "-"},
            )
        product_id = product.product_id

    try:
        current = Basket.from_api(await data.client.get_basket())
        # AddToBasket sets an absolute quantity; this action adds to what is there.
        target = current.quantity_of(product_id) + quantity
        basket = Basket.from_api(
            await data.client.set_basket_quantity(product_id, target)
        )
    except NemligError as err:
        raise _api_error(err) from err

    data.basket.async_set_updated_data(basket)
    line = basket.line(product_id)
    if line is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="product_not_added",
            translation_placeholders={"product_id": product_id},
        )
    return {
        "status": "added",
        "product_id": product_id,
        "name": line.name,
        "quantity": line.quantity,
        **_basket_summary(basket),
    }


async def _add_shopping_list(call: ServiceCall) -> ServiceResponse:
    """Add every product of a saved nemlig.com shopping list to the live basket."""
    data = _data(call)
    wanted: str = call.data[ATTR_LIST].strip()
    try:
        lists = (await data.client.get_shopping_lists(take=50)).get(
            "ShoppingListOverViewViewModels"
        ) or []
    except NemligError as err:
        raise _api_error(err) from err

    match = next(
        (
            item
            for item in lists
            if str(item.get("Id")) == wanted
            or str(item.get("Name", "")).casefold() == wanted.casefold()
        ),
        None,
    )
    if match is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="list_not_found",
            translation_placeholders={
                "list": wanted,
                "options": "; ".join(str(item.get("Name")) for item in lists) or "-",
            },
        )
    try:
        basket = Basket.from_api(
            await data.client.add_shopping_list_to_basket(int(match["Id"]))
        )
    except NemligError as err:
        raise _api_error(err) from err

    data.basket.async_set_updated_data(basket)
    return {"status": "added", "list": match.get("Name"), **_basket_summary(basket)}


async def _search(call: ServiceCall) -> ServiceResponse:
    """Search nemlig.com's catalogue."""
    data = _data(call)
    try:
        found = await data.client.search_products(
            call.data[ATTR_QUERY], limit=call.data[ATTR_LIMIT]
        )
    except NemligError as err:
        raise _api_error(err) from err
    return {"products": [_product(p) for p in found]}
