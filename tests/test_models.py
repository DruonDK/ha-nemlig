"""Tests for parsing delivery data and deriving the order status."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from custom_components.nemlig.models import (
    DeliveryData,
    DeliverySpot,
    Order,
    OrderStatus,
)

from .conftest import load_fixture

# 2026-11-02 in UTC; the fixture's window is 08:00-13:00Z (09-14 Danish time).
BEFORE_DEADLINE = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
DELIVERY_MORNING = datetime(2026, 11, 2, 7, 30, tzinfo=UTC)
NEXT_WEEK = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _data(
    spot: dict[str, Any] | None = None, statuses: tuple[int, int] = (2, 3)
) -> DeliveryData:
    history = load_fixture("order_history.json")["Orders"]
    for order, status in zip(history, statuses, strict=True):
        order["Status"] = status
    raw_spot = load_fixture("delivery_spot.json") if spot is None else spot
    return DeliveryData(
        orders=[Order.from_api(o) for o in history],
        spot=DeliverySpot.from_api(raw_spot) if raw_spot else None,
    )


def test_history_local_time_matches_spot_utc() -> None:
    """Order history's naive local time and DeliverySpot's UTC are the same instant."""
    data = _data()
    order = data.upcoming
    assert order is not None
    assert order.window_start == data.spot.window_start
    assert order.window_start.isoformat() == "2026-11-02T09:00:00+01:00"


def test_eta_sentinel_is_none() -> None:
    """The 0001..9999 placeholder interval means no estimate."""
    raw = load_fixture("delivery_spot.json")
    raw["DeliveryInterval"] = {
        "Start": "0001-01-01T00:00:00Z",
        "End": "9999-12-31T22:59:59Z",
    }
    raw["DeliveryTime"] = None
    spot = DeliverySpot.from_api(raw)
    assert spot.eta is None
    assert spot.eta_start is None
    assert spot.eta_end is None


def test_upcoming_is_oldest_pending() -> None:
    """With two pending orders, the next delivery is the older one."""
    data = _data(statuses=(1, 2))
    assert data.upcoming is not None
    assert data.upcoming.id == 12345600


@pytest.mark.parametrize(
    ("state", "now", "expected"),
    [
        ("ReadyForDelivery", DELIVERY_MORNING, OrderStatus.READY_FOR_DELIVERY),
        ("OngoingDelivery", DELIVERY_MORNING, OrderStatus.OUT_FOR_DELIVERY),
        ("SomethingNew", BEFORE_DEADLINE, OrderStatus.EDITABLE),
        ("SomethingNew", DELIVERY_MORNING, OrderStatus.PROCESSING),
    ],
)
def test_status_from_spot(state: str, now: datetime, expected: str) -> None:
    """Known DeliverySpot states map directly; unknown ones fall back on the deadline."""
    raw = load_fixture("delivery_spot.json")
    raw["State"] = state
    assert _data(raw).status(now) == expected


def test_status_delivered_today_then_none() -> None:
    """A delivered order shows as delivered on its day, then as no order."""
    data = _data(statuses=(3, 3))
    assert data.upcoming is None
    assert data.status(DELIVERY_MORNING) == OrderStatus.DELIVERED
    assert data.status(NEXT_WEEK) == OrderStatus.NONE


def test_status_delivered_from_spot() -> None:
    """DeliverySpot reports the drop-off before the order history does."""
    raw = load_fixture("delivery_spot.json")
    raw["State"] = "CompletedDelivery"
    data = _data(raw, statuses=(2, 3))
    assert data.upcoming is None
    assert data.status(DELIVERY_MORNING) == OrderStatus.DELIVERED


def test_spot_for_other_order_is_ignored() -> None:
    """DeliverySpot only counts when it describes the upcoming order."""
    raw = load_fixture("delivery_spot.json")
    raw["Id"] = "999"
    data = _data(raw)
    assert data.upcoming_spot is None
    assert data.edit_deadline(BEFORE_DEADLINE) is None
    assert data.window_start is not None  # falls back on the order history


def test_edit_deadline_passes() -> None:
    """The edit deadline is shown until it passes."""
    data = _data()
    assert data.edit_deadline(BEFORE_DEADLINE) == datetime(
        2026, 11, 1, 19, 59, tzinfo=UTC
    )
    assert data.edit_deadline(DELIVERY_MORNING) is None
