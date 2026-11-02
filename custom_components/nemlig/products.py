"""The "bought before" product index used to turn free text into a product.

Built from the line items of the most recent delivered orders and kept in HA's storage,
so a restart doesn't refetch them. Matching only picks a product when the choice is
clear; otherwise it hands back candidates instead of guessing, because the basket is
real and shared with the nemlig.com app.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import NemligClient
from .const import DOMAIN
from .models import Order

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
ORDERS_TO_INDEX = 15
# Rebuild at setup when the index is older than this; deliveries also trigger it.
MAX_AGE = timedelta(days=1)
MAX_CANDIDATES = 5
# Danish endings allowed after a query word: "æble" -> "æbler", "tomat" -> "tomater".
INFLECTIONS = ("", "e", "r", "er", "en", "et", "ne")
# Shortest first part of a compound: "rug" in "rugbrød"; rules out "fr" + "ost".
MIN_MODIFIER_LETTERS = 3


@dataclass(frozen=True, slots=True)
class IndexedProduct:
    """A product the household has bought before."""

    product_id: str
    name: str
    description: str
    group: str
    times_bought: int
    last_bought: str | None

    def as_dict(self) -> dict[str, Any]:
        """For action responses."""
        return {
            "product_id": self.product_id,
            "name": self.name,
            "description": self.description,
            "times_bought": self.times_bought,
            "last_bought": self.last_bought,
        }


class ProductIndex:
    """Products from the last ORDERS_TO_INDEX delivered orders."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, client: NemligClient
    ) -> None:
        """Initialize the index."""
        self._client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.products"
        )
        # order id -> {"date": iso date | None, "lines": [line dicts]}
        self._orders: dict[str, dict[str, Any]] = {}
        self.products: dict[str, IndexedProduct] = {}
        self.updated: datetime | None = None

    @property
    def stale(self) -> bool:
        """Whether the index should be rebuilt."""
        return self.updated is None or dt_util.utcnow() - self.updated > MAX_AGE

    async def async_load(self) -> None:
        """Load the stored index."""
        if stored := await self._store.async_load():
            self._orders = stored.get("orders") or {}
            self.updated = dt_util.parse_datetime(stored.get("updated") or "")
            self._rebuild()

    async def async_refresh(self, delivered_ids: Collection[int] = ()) -> None:
        """Fetch line items for newly delivered orders and drop the oldest.

        `delivered_ids` are orders known to be delivered that the order history may still
        list as status 2, which it does for hours after the drop-off. Only orders not yet
        indexed are fetched, so a refresh after a delivery costs two requests. Raises
        NemligError when nemlig.com fails.
        """
        history = await self._client.get_order_history(take=ORDERS_TO_INDEX + 5)
        orders = [Order.from_api(o) for o in (history or {}).get("Orders") or []]
        delivered = [o for o in orders if o.delivered or o.id in delivered_ids][
            :ORDERS_TO_INDEX
        ]

        kept: dict[str, dict[str, Any]] = {}
        for order in delivered:
            key = str(order.id)
            if key not in self._orders:
                details = await self._client.get_order_details(order.id)
                self._orders[key] = {
                    "date": order.window_start.date().isoformat()
                    if order.window_start
                    else None,
                    "lines": [
                        _line(line) for line in (details or {}).get("Lines") or []
                    ],
                }
            kept[key] = self._orders[key]
        self._orders = kept
        self.updated = dt_util.utcnow()
        self._rebuild()
        await self._store.async_save(
            {"orders": self._orders, "updated": self.updated.isoformat()}
        )
        _LOGGER.debug(
            "Product index: %d products from %d orders", len(self.products), len(kept)
        )

    def _rebuild(self) -> None:
        counts: dict[str, int] = {}
        latest: dict[str, tuple[str | None, dict[str, Any]]] = {}
        for order in self._orders.values():
            seen: set[str] = set()
            for line in order["lines"]:
                pid = line["product_id"]
                if pid in seen:
                    continue
                seen.add(pid)
                counts[pid] = counts.get(pid, 0) + 1
                previous = latest.get(pid)
                if previous is None or (order["date"] or "") > (previous[0] or ""):
                    latest[pid] = (order["date"], line)
        self.products = {
            pid: IndexedProduct(
                product_id=pid,
                name=line["name"],
                description=line["description"],
                group=line["group"],
                times_bought=counts[pid],
                last_bought=date,
            )
            for pid, (date, line) in latest.items()
        }

    def candidates(self, query: str) -> list[IndexedProduct]:
        """Products matching every word of the query, most bought first."""
        words = _normalize(query).split()
        if not words:
            return []
        found = []
        for product in self.products.values():
            text = _normalize(f"{product.name} {product.description} {product.group}")
            if all(
                any(_word_matches(word, candidate) for candidate in text.split())
                for word in words
            ):
                found.append(product)
        return sorted(
            found,
            key=lambda p: (p.times_bought, p.last_bought or ""),
            reverse=True,
        )

    def resolve(self, query: str) -> tuple[IndexedProduct | None, list[IndexedProduct]]:
        """Pick the product a query means, or return the candidates when unclear.

        A pick needs an exact name match, a single candidate, or a clear favourite:
        bought at least twice and at least twice as often as the runner-up.
        """
        found = self.candidates(query)
        if not found:
            return None, []
        exact = [p for p in found if _normalize(p.name) == _normalize(query)]
        if len(exact) == 1:
            return exact[0], found[:MAX_CANDIDATES]
        if len(found) == 1:
            return found[0], found
        top, runner_up = found[0], found[1]
        if top.times_bought >= 2 and top.times_bought >= 2 * runner_up.times_bought:
            return top, found[:MAX_CANDIDATES]
        return None, found[:MAX_CANDIDATES]

    def as_dict(self) -> dict[str, Any]:
        """For diagnostics."""
        return {
            "updated": self.updated.isoformat() if self.updated else None,
            "orders": len(self._orders),
            "products": [asdict(p) for p in self.products.values()],
        }


def _line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": str(line["ProductNumber"]),
        "name": str(line.get("ProductName") or ""),
        "description": str(line.get("Description") or ""),
        "group": str(line.get("GroupName") or ""),
    }


def _word_matches(query: str, word: str) -> bool:
    """Whether a product word is the query word, Danish-style.

    Danish compounds put the head last: "skummetmælk" is milk, "smørbagt" is not
    butter and "frostvarer" is not cheese. So the query must end the word (as the
    compound's head, from three letters, after a modifier of at least three: "rug+brød"
    but not "fr+ost"), followed only by a Danish ending ("cherrytomat+er", but not
    "leverpost+ej").
    """
    for ending in INFLECTIONS:
        if not word.endswith(ending):
            continue
        stem = word[: len(word) - len(ending)]
        if stem == query or (
            len(query) >= 3
            and len(stem) - len(query) >= MIN_MODIFIER_LETTERS
            and stem.endswith(query)
        ):
            return True
    return False


def _normalize(text: str) -> str:
    return re.sub(r"[^\w%]+", " ", text.casefold()).strip()
