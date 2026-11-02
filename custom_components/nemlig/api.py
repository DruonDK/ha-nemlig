"""Async client for the undocumented nemlig.com web API.

Endpoints and payloads follow nemlig_api.md in DruonDK/nemlig_cli, which is the source of
truth. This module implements only what the integration uses and returns the API's own
JSON; turning it into entity state is the coordinators' job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import aiohttp

from .const import BASE_URL, SEARCH_API_URL

_LOGGER = logging.getLogger(__name__)

# Bearer tokens expire after ~5 min; renew a little before that.
TOKEN_LIFETIME = 270
# The API is undocumented and not ours: at most ~2 requests per second.
MIN_REQUEST_INTERVAL = 0.5
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=5)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
# The web client's version header; the API has accepted this value since 2025.
CLIENT_VERSION = "11.201.0"


class NemligError(Exception):
    """Base error for the nemlig.com client."""


class NemligAuthError(NemligError):
    """The credentials or the session were rejected."""


class NemligConnectionError(NemligError):
    """nemlig.com could not be reached or answered with a server error."""


class NemligResponseError(NemligError):
    """nemlig.com answered with an unexpected 4xx status or body."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _Unauthorized(Exception):
    """HTTP 401/403; internal signal to log in again."""


class NemligClient:
    """One logged-in nemlig.com account.

    The session must be dedicated to this client: login sets cookies (basket key and
    others) that must not leak into, or be cleared from, a shared session.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        min_interval: float = MIN_REQUEST_INTERVAL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._min_interval = min_interval
        self._bearer: str | None = None
        self._xsrf: str | None = None
        self._token_time = 0.0
        self._last_request = 0.0
        self._throttle_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()

    async def async_login(self) -> None:
        """Log in now, replacing any current session."""
        async with self._login_lock:
            await self._login()

    async def get_delivery_spot(self) -> dict[str, Any] | None:
        """Current/next order with time slot, edit deadline and delivery estimate."""
        return await self._request("GET", f"{BASE_URL}/webapi/Order/DeliverySpot")

    async def get_order_history(self, skip: int = 0, take: int = 10) -> dict[str, Any]:
        """Orders, newest first, including the upcoming one (`Orders`, `NumberOfPages`)."""
        return await self._request(
            "GET",
            f"{BASE_URL}/webapi/order/GetBasicOrderHistory",
            params={"skip": skip, "take": take},
        )

    async def get_order_details(self, order_id: int) -> dict[str, Any]:
        """Line items of one order (`Lines`)."""
        return await self._request(
            "GET", f"{BASE_URL}/webapi/v2/order/GetOrderHistory/{order_id}"
        )

    async def get_basket(self) -> dict[str, Any]:
        """The account's live basket. It is shared with the nemlig.com app and website."""
        return await self._request("GET", f"{BASE_URL}/webapi/basket/GetBasket")

    async def set_basket_quantity(
        self, product_id: str, quantity: int
    ) -> dict[str, Any]:
        """Set a product's absolute quantity in the live basket. Returns the basket."""
        return await self._request(
            "POST",
            f"{BASE_URL}/webapi/basket/AddToBasket",
            payload={
                "ProductId": product_id,
                "quantity": quantity,
                "AffectPartialQuantity": False,
                "disableQuantityValidation": False,
            },
        )

    async def remove_from_basket(self, product_id: str) -> dict[str, Any]:
        """Remove a product line from the live basket. Returns the basket.

        There is no delete endpoint; the web app sends quantity 0 with
        AffectPartialQuantity set.
        """
        return await self._request(
            "POST",
            f"{BASE_URL}/webapi/basket/AddToBasket",
            payload={
                "ProductId": product_id,
                "quantity": 0,
                "AffectPartialQuantity": True,
                "disableQuantityValidation": False,
            },
        )

    async def get_shopping_lists(self, skip: int = 0, take: int = 20) -> dict[str, Any]:
        """Saved shopping lists (`ShoppingListOverViewViewModels`, `NumberOfPages`)."""
        return await self._request(
            "GET",
            f"{BASE_URL}/webapi/ShoppingList/GetShoppingLists",
            params={"skip": skip, "take": take},
        )

    async def add_shopping_list_to_basket(
        self, list_id: int, confirm_missing: bool = False
    ) -> dict[str, Any]:
        """Add every product of a saved list to the live basket. Returns the basket."""
        return await self._request(
            "POST",
            f"{BASE_URL}/webapi/basket/addShoppingListToBasket",
            payload={"ListId": list_id, "ConfirmMissingProducts": confirm_missing},
        )

    async def search_products(
        self, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Full product search through the separate search gateway."""
        app = await self._request("GET", f"{BASE_URL}/webapi/v2/AppSettings/Website")
        page = await self._request(
            "GET", f"{BASE_URL}/", params={"GetAsJson": "1", "d": "1"}
        )
        settings = (page or {}).get("Settings") or {}
        params: dict[str, Any] = {
            "query": query,
            "take": limit,
            "skip": 0,
            "recipeCount": 0,
            "timestamp": (app or {}).get("CombinedProductsAndSitecoreTimestamp", ""),
            "timeslotUtc": settings.get("TimeslotUtc", ""),
            "deliveryZoneId": settings.get("DeliveryZoneId", 1),
        }
        if user_id := settings.get("UserId"):
            params["includeFavorites"] = user_id
        data = await self._request(
            "GET", f"{SEARCH_API_URL}/search", params=params, search=True
        )
        return ((data or {}).get("Products") or {}).get("Products") or []

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        search: bool = False,
    ) -> Any:
        """Authenticated request; a rejected token triggers one fresh login and retry."""
        await self._ensure_login()
        try:
            return await self._send(
                method,
                url,
                headers=self._headers(search),
                params=params,
                payload=payload,
            )
        except _Unauthorized:
            _LOGGER.debug("Session rejected on %s %s, logging in again", method, url)
        async with self._login_lock:
            await self._login()
        try:
            return await self._send(
                method,
                url,
                headers=self._headers(search),
                params=params,
                payload=payload,
            )
        except _Unauthorized as err:
            raise NemligAuthError("Session rejected right after a fresh login") from err

    async def _ensure_login(self) -> None:
        if self._token_valid():
            return
        async with self._login_lock:
            # Another task may have logged in while this one waited for the lock.
            if not self._token_valid():
                await self._login()

    def _token_valid(self) -> bool:
        return (
            self._bearer is not None
            and time.monotonic() - self._token_time < TOKEN_LIFETIME
        )

    async def _login(self) -> None:
        """XSRF token -> bearer token -> login, then fresh tokens for the new session.

        Mirrors the web app's header sequence step by step (nemlig_api.md, Authentication
        Flow). Logging in does not touch the basket: it belongs to the account, not the
        session (verified 2026-11-02).
        """
        self._bearer = self._xsrf = None
        self._session.cookie_jar.clear()
        headers = _base_headers()
        try:
            xsrf = (
                await self._send(
                    "GET", f"{BASE_URL}/webapi/AntiForgery", headers=headers
                )
            )["Value"]
            headers["X-Correlation-Id"] = str(uuid.uuid4())
            bearer = (
                await self._send("GET", f"{BASE_URL}/webapi/Token", headers=headers)
            )["access_token"]

            headers["X-Correlation-Id"] = str(uuid.uuid4())
            headers["X-XSRF-TOKEN"] = xsrf
            headers["Authorization"] = f"Bearer {bearer}"
            headers["Referer"] = f"{BASE_URL}/login?returnUrl=%2F"
            try:
                result = await self._send(
                    "POST",
                    f"{BASE_URL}/webapi/login",
                    headers=headers,
                    payload={
                        "Username": self._username,
                        "Password": self._password,
                        "CheckForExistingProducts": True,
                        "DoMerge": True,
                        "AppInstalled": False,
                        "SaveExistingBasket": False,
                    },
                )
            except (_Unauthorized, NemligResponseError) as err:
                raise NemligAuthError("Login rejected") from err
            if not isinstance(result, dict) or "RedirectUrl" not in result:
                raise NemligAuthError("Login rejected")

            headers["X-Correlation-Id"] = str(uuid.uuid4())
            bearer = (
                await self._send("GET", f"{BASE_URL}/webapi/Token", headers=headers)
            )["access_token"]
            xsrf = (
                await self._send(
                    "GET", f"{BASE_URL}/webapi/AntiForgery", headers=headers
                )
            )["Value"]
        except _Unauthorized as err:
            raise NemligAuthError("Token request rejected during login") from err
        except (KeyError, TypeError) as err:
            raise NemligResponseError(f"Unexpected login response: {err!r}") from err

        self._bearer, self._xsrf = bearer, xsrf
        self._token_time = time.monotonic()

    def _headers(self, search: bool) -> dict[str, str]:
        if search:
            # The search gateway is another host; it takes the bearer token only.
            return {
                "Accept": "application/json, text/plain, */*",
                "Authorization": f"Bearer {self._bearer}",
                "X-Correlation-Id": str(uuid.uuid4()),
                "Referer": f"{BASE_URL}/",
                "User-Agent": USER_AGENT,
            }
        return {
            **_base_headers(),
            "Authorization": f"Bearer {self._bearer}",
            "X-XSRF-TOKEN": self._xsrf or "",
            "Referer": f"{BASE_URL}/",
        }

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        await self._throttle()
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise _Unauthorized
                if resp.status >= 500:
                    raise NemligConnectionError(f"{method} {url}: HTTP {resp.status}")
                if resp.status >= 400:
                    raise NemligResponseError(
                        f"{method} {url}: HTTP {resp.status}", resp.status
                    )
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NemligConnectionError(f"{method} {url}: {err!r}") from err
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as err:
            raise NemligResponseError(f"{method} {url}: response is not JSON") from err

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            wait = self._last_request + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()


def _base_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Device-Size": "desktop",
        "Platform": "web",
        "Version": CLIENT_VERSION,
        "X-Correlation-Id": str(uuid.uuid4()),
    }
