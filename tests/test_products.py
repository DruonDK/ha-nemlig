"""Tests for the "bought before" product index."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nemlig.products import ProductIndex, _word_matches

from .conftest import load_fixture


def _line(pid: str, name: str, group: str = "", description: str = "") -> dict:
    return {"product_id": pid, "name": name, "description": description, "group": group}


@pytest.fixture
def index(hass: HomeAssistant) -> ProductIndex:
    """An index over six orders: semi-skimmed milk in all, cocoa milk in two."""
    index = ProductIndex(hass, "entry", MagicMock())
    milk = _line("1", "Letmælk 0,5% øko.", "Mælk", "1 l / Arla")
    cocoa = _line("2", "Cocio kakaomælk", "Kakaomælk", "0,25 l / Classic")
    coffee = _line("3", "Black Coffee Blend Supreme", "Kaffe, hele bønner")
    oat = _line("4", "Havredrik", "Plantedrik")
    oat_barista = _line("5", "Havredrik barista", "Plantedrik")
    index._orders = {
        str(n): {
            "date": f"2026-10-{10 + n:02d}",
            "lines": [milk]
            + ([cocoa] if n < 2 else [])
            + ([coffee] if n == 0 else [])
            + ([oat, oat_barista] if n == 3 else []),
        }
        for n in range(6)
    }
    index._rebuild()
    return index


def test_counts(index: ProductIndex) -> None:
    """A product counts once per order it appears in."""
    assert index.products["1"].times_bought == 6
    assert index.products["2"].times_bought == 2
    assert index.products["1"].last_bought == "2026-10-15"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("mælk", "1"),  # letmælk bought 3x as often as kakaomælk
        ("cocio", "2"),  # single candidate
        ("æble", None),  # nothing called æble bought
        ("KAFFE", "3"),  # group name, any case
        ("cocio kakaomælk", "2"),  # exact name
        ("havredrik", "4"),  # exact name beats an equally bought variant
    ],
)
def test_resolve_clear(index: ProductIndex, query: str, expected: str | None) -> None:
    """Clear choices resolve to one product."""
    product, _ = index.resolve(query)
    assert (product.product_id if product else None) == expected


def test_resolve_ambiguous(index: ProductIndex) -> None:
    """Equally bought matches return candidates instead of a guess."""
    product, candidates = index.resolve("drik")
    assert product is None
    assert {c.product_id for c in candidates} == {"4", "5"}


def test_resolve_unknown(index: ProductIndex) -> None:
    """Nothing bought before matches."""
    assert index.resolve("gær") == (None, [])
    assert index.resolve("   ") == (None, [])


async def test_refresh_fetches_only_new_orders(hass: HomeAssistant) -> None:
    """A refresh fetches line items once per delivered order and persists them."""
    client = MagicMock()
    client.get_order_history = AsyncMock(
        return_value=load_fixture("order_history.json")
    )
    client.get_order_details = AsyncMock(
        return_value=load_fixture("order_details.json")
    )
    index = ProductIndex(hass, "entry", client)

    await index.async_refresh()
    await index.async_refresh()

    # Only the delivered order (status 3) is indexed, and fetched once.
    client.get_order_details.assert_awaited_once_with(12345600)
    assert set(index.products) == {"5064654", "701025", "5000123"}
    assert not index.stale({12345600})
    assert index.stale({12345678})  # delivered, but not indexed yet

    reloaded = ProductIndex(hass, "entry", client)
    await reloaded.async_load()
    assert set(reloaded.products) == set(index.products)


@pytest.mark.parametrize(
    ("query", "word", "matches"),
    [
        ("mælk", "skummetmælk", True),  # compound head
        ("æble", "æbler", True),  # plural
        ("banan", "bananer", True),
        ("smør", "smørbagt", False),  # compound modifier, not butter
        ("ost", "frostvarer", False),
        ("ost", "frost", False),  # "fr" is no modifier
        ("ost", "hytteost", True),
        ("brød", "rugbrød", True),
        ("ost", "leverpostej", False),  # "ej" is no Danish ending
        ("tomat", "cherrytomater", True),
        ("æg", "ægnudler", False),
        ("æg", "æg", True),
        ("æg", "pålæg", False),  # two-letter words only match whole
        ("mælk", "sødmælksfranskbrød", False),
    ],
)
def test_word_matches(query: str, word: str, matches: bool) -> None:
    """Danish compounds match on their head, not on a modifier."""
    assert _word_matches(query, word) is matches


async def test_refresh_includes_orders_known_delivered(hass: HomeAssistant) -> None:
    """An order DeliverySpot reports delivered is indexed before the history says so."""
    client = MagicMock()
    client.get_order_history = AsyncMock(
        return_value=load_fixture("order_history.json")
    )
    client.get_order_details = AsyncMock(
        return_value=load_fixture("order_details.json")
    )
    index = ProductIndex(hass, "entry", client)

    await index.async_refresh({12345678})

    assert [c.args for c in client.get_order_details.await_args_list] == [
        (12345678,),
        (12345600,),
    ]
