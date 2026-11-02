"""Tests for the nemlig.com API client."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import aiohttp
import pytest
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.nemlig.api import (
    TOKEN_LIFETIME,
    NemligAuthError,
    NemligClient,
    NemligConnectionError,
    NemligResponseError,
)

from .conftest import BASE_URL, PASSWORD, SEARCH_URL, USERNAME, load_fixture, mock_login

BASKET_URL = f"{BASE_URL}/webapi/basket/GetBasket"
ADD_URL = f"{BASE_URL}/webapi/basket/AddToBasket"
LOGIN_CALLS = 5


def _calls(aioclient_mock: AiohttpClientMocker, method: str, url: str) -> list:
    return [
        c
        for c in aioclient_mock.mock_calls
        if c[0].lower() == method and str(c[1]).startswith(url)
    ]


async def test_login_flow(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Login runs the web app's 3-step flow and authenticates later requests."""
    mock_login(aioclient_mock)
    aioclient_mock.get(BASKET_URL, json=load_fixture("basket.json"))

    await client.async_login()
    assert aioclient_mock.call_count == LOGIN_CALLS
    _, _, payload, headers = _calls(aioclient_mock, "post", f"{BASE_URL}/webapi/login")[
        0
    ]
    assert payload["Username"] == USERNAME
    assert payload["Password"] == PASSWORD
    assert headers["Authorization"] == "Bearer bearer-token-1"
    assert headers["X-XSRF-TOKEN"] == "xsrf-token-1"

    basket = await client.get_basket()
    assert basket["Lines"][0]["Id"] == "701025"
    _, _, _, headers = _calls(aioclient_mock, "get", BASKET_URL)[0]
    assert headers["Authorization"] == "Bearer bearer-token-1"
    assert headers["X-XSRF-TOKEN"] == "xsrf-token-1"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (
            HTTPStatus.BAD_REQUEST,
            {"ErrorMessage": "Forkert brugernavn eller adgangskode"},
        ),
        (HTTPStatus.UNAUTHORIZED, {}),
        (HTTPStatus.OK, {"ErrorMessage": "Forkert brugernavn eller adgangskode"}),
    ],
)
async def test_login_rejected(
    client: NemligClient, aioclient_mock: AiohttpClientMocker, status: int, body: dict
) -> None:
    """A rejected login, or one without RedirectUrl, is an auth error."""
    mock_login(aioclient_mock, login_status=status, login_json=body)
    with pytest.raises(NemligAuthError):
        await client.async_login()


async def test_login_unexpected_response(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A changed token endpoint is reported as a response error, not bad credentials."""
    aioclient_mock.get(f"{BASE_URL}/webapi/AntiForgery", json={"Unexpected": True})
    with pytest.raises(NemligResponseError):
        await client.async_login()


async def test_token_reused_then_renewed(
    client: NemligClient,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The token is reused within its lifetime and renewed after it."""
    mock_login(aioclient_mock)
    aioclient_mock.get(BASKET_URL, json=load_fixture("basket.json"))

    await client.get_basket()
    await client.get_basket()
    assert aioclient_mock.call_count == LOGIN_CALLS + 2

    freezer.tick(timedelta(seconds=TOKEN_LIFETIME + 1))
    await client.get_basket()
    assert aioclient_mock.call_count == 2 * LOGIN_CALLS + 3


async def test_relogin_on_unauthorized(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 triggers one fresh login and a retry."""
    mock_login(aioclient_mock)
    responses = iter([HTTPStatus.UNAUTHORIZED, HTTPStatus.OK])

    async def basket(method, url, data):
        return AiohttpClientMockResponse(
            method, url, status=next(responses), json=load_fixture("basket.json")
        )

    aioclient_mock.get(BASKET_URL, side_effect=basket)

    basket_data = await client.get_basket()
    assert basket_data["BasketGuid"]
    assert len(_calls(aioclient_mock, "post", f"{BASE_URL}/webapi/login")) == 2


async def test_unauthorized_after_relogin(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 right after a fresh login is an auth error, not a loop."""
    mock_login(aioclient_mock)
    aioclient_mock.get(BASKET_URL, status=HTTPStatus.FORBIDDEN)
    with pytest.raises(NemligAuthError):
        await client.get_basket()
    assert len(_calls(aioclient_mock, "post", f"{BASE_URL}/webapi/login")) == 2


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": HTTPStatus.INTERNAL_SERVER_ERROR},
        {"status": HTTPStatus.BAD_GATEWAY},
        {"exc": aiohttp.ClientConnectionError()},
        {"exc": TimeoutError()},
    ],
)
async def test_connection_errors(
    client: NemligClient, aioclient_mock: AiohttpClientMocker, mock_kwargs: dict
) -> None:
    """Server errors and network failures are connection errors."""
    mock_login(aioclient_mock)
    aioclient_mock.get(BASKET_URL, **mock_kwargs)
    with pytest.raises(NemligConnectionError):
        await client.get_basket()


async def test_empty_body(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty 200 response becomes None."""
    mock_login(aioclient_mock)
    aioclient_mock.get(f"{BASE_URL}/webapi/Order/DeliverySpot", text="")
    assert await client.get_delivery_spot() is None


async def test_delivery_spot_and_history(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Delivery data is returned as the API's JSON."""
    mock_login(aioclient_mock)
    aioclient_mock.get(
        f"{BASE_URL}/webapi/Order/DeliverySpot", json=load_fixture("delivery_spot.json")
    )
    aioclient_mock.get(
        f"{BASE_URL}/webapi/order/GetBasicOrderHistory",
        params={"skip": 0, "take": 5},
        json=load_fixture("order_history.json"),
    )

    spot = await client.get_delivery_spot()
    assert spot["State"] == "ReadyForDelivery"
    history = await client.get_order_history(take=5)
    assert history["Orders"][0]["Status"] == 2


async def test_basket_writes(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Setting a quantity and removing a line send the web app's payloads."""
    mock_login(aioclient_mock)
    aioclient_mock.post(ADD_URL, json=load_fixture("basket.json"))

    await client.set_basket_quantity("701025", 3)
    await client.remove_from_basket("701025")

    set_call, remove_call = _calls(aioclient_mock, "post", ADD_URL)
    assert set_call[2] == {
        "ProductId": "701025",
        "quantity": 3,
        "AffectPartialQuantity": False,
        "disableQuantityValidation": False,
    }
    assert remove_call[2]["quantity"] == 0
    assert remove_call[2]["AffectPartialQuantity"] is True


async def test_search(
    client: NemligClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Search passes page settings to the search gateway with the bearer token only."""
    mock_login(aioclient_mock)
    aioclient_mock.get(
        f"{BASE_URL}/webapi/v2/AppSettings/Website",
        json=load_fixture("app_settings.json"),
    )
    aioclient_mock.get(f"{BASE_URL}/", json=load_fixture("page.json"))
    aioclient_mock.get(SEARCH_URL, json=load_fixture("search.json"))

    products = await client.search_products("cocio", limit=3)

    assert [p["Id"] for p in products] == ["701025"]
    _, url, _, headers = _calls(aioclient_mock, "get", SEARCH_URL)[0]
    assert url.query["query"] == "cocio"
    assert url.query["take"] == "3"
    assert url.query["timeslotUtc"] == "2026110209-300-900"
    assert url.query["includeFavorites"] == "999999"
    assert headers["Authorization"] == "Bearer bearer-token-1"
    assert "X-XSRF-TOKEN" not in headers
