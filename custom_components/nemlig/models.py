"""Parsed nemlig.com delivery data.

The API mixes time formats: DeliverySpot returns UTC with a `Z`, while the order history
returns naive Danish local time (`2026-11-02T09:00:00` for the slot that DeliverySpot
calls `2026-11-02T08:00:00Z`). Everything here is converted to aware datetimes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

NEMLIG_TZ = ZoneInfo("Europe/Copenhagen")

# GetBasicOrderHistory status codes for orders that have been delivered.
DELIVERED_STATUSES = {3, 4}

# DeliverySpot `State` values seen in live traffic (2026-11-02), mapped to OrderStatus.
SPOT_STATES = {
    "ReadyForDelivery": "ready_for_delivery",
    "OngoingDelivery": "out_for_delivery",
}


class OrderStatus:
    """Values of the order status sensor (an enum sensor's options)."""

    NONE = "none"
    EDITABLE = "editable"
    PROCESSING = "processing"
    READY_FOR_DELIVERY = "ready_for_delivery"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"

    OPTIONS = [
        NONE,
        EDITABLE,
        PROCESSING,
        READY_FOR_DELIVERY,
        OUT_FOR_DELIVERY,
        DELIVERED,
    ]


@dataclass(frozen=True, slots=True)
class Order:
    """One order from GetBasicOrderHistory."""

    id: int
    number: str
    status_code: int
    total: float | None
    window_start: datetime | None
    window_end: datetime | None
    editable: bool

    @property
    def delivered(self) -> bool:
        """Whether nemlig.com reports the order as delivered."""
        return self.status_code in DELIVERED_STATUSES

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Order:
        """Parse one entry of `Orders`."""
        window = data.get("DeliveryTime") or {}
        return cls(
            id=int(data["Id"]),
            number=str(data.get("OrderNumber") or ""),
            status_code=int(data.get("Status") or 0),
            total=data.get("Total"),
            window_start=parse_local(window.get("Start")),
            window_end=parse_local(window.get("End")),
            editable=bool(data.get("IsEditable")),
        )


@dataclass(frozen=True, slots=True)
class DeliverySpot:
    """The current/next order as DeliverySpot reports it."""

    order_id: int | None
    state: str | None
    progress: float | None
    window_start: datetime | None
    window_end: datetime | None
    edit_deadline: datetime | None
    eta: datetime | None
    eta_start: datetime | None
    eta_end: datetime | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DeliverySpot:
        """Parse the DeliverySpot response."""
        slot = data.get("TimeSlot") or {}
        interval = data.get("DeliveryInterval") or {}
        eta_start = parse_utc(interval.get("Start"))
        eta_end = parse_utc(interval.get("End"))
        if (
            eta_start is None
            or eta_end is None
            or eta_start.year < 2000
            or eta_end.year > 9000
        ):
            # Without an estimate the API sends 0001-01-01 .. 9999-12-31.
            eta_start = eta_end = None
        order_id = data.get("Id")
        return cls(
            order_id=int(order_id) if order_id else None,
            state=data.get("State"),
            progress=data.get("Progress"),
            window_start=parse_utc(slot.get("Start")),
            window_end=parse_utc(slot.get("End")),
            edit_deadline=parse_utc(data.get("EditDeadline")),
            eta=parse_utc(data.get("DeliveryTime")),
            eta_start=eta_start,
            eta_end=eta_end,
        )


@dataclass(frozen=True, slots=True)
class DeliveryData:
    """Everything the delivery entities show, from one coordinator update."""

    orders: list[Order] = field(default_factory=list)
    spot: DeliverySpot | None = None

    @property
    def upcoming(self) -> Order | None:
        """The next order not yet delivered (the API lists newest first)."""
        pending = [o for o in self.orders if not o.delivered]
        return pending[-1] if pending else None

    @property
    def latest(self) -> Order | None:
        """The most recent order, delivered or not."""
        return self.orders[0] if self.orders else None

    @property
    def upcoming_spot(self) -> DeliverySpot | None:
        """DeliverySpot, if it describes the upcoming order."""
        order = self.upcoming
        if order is None or self.spot is None or self.spot.order_id != order.id:
            return None
        return self.spot

    @property
    def window_start(self) -> datetime | None:
        """Start of the upcoming order's delivery window; DeliverySpot's UTC wins."""
        if (spot := self.upcoming_spot) and spot.window_start:
            return spot.window_start
        return order.window_start if (order := self.upcoming) else None

    @property
    def window_end(self) -> datetime | None:
        """End of the upcoming order's delivery window."""
        if (spot := self.upcoming_spot) and spot.window_end:
            return spot.window_end
        return order.window_end if (order := self.upcoming) else None

    def edit_deadline(self, now: datetime) -> datetime | None:
        """When the upcoming order stops being editable; None once that has passed."""
        spot = self.upcoming_spot
        if spot is None or spot.edit_deadline is None or spot.edit_deadline <= now:
            return None
        return spot.edit_deadline

    def status(self, now: datetime) -> str:
        """Overall order status, robust to DeliverySpot states not seen yet."""
        if (order := self.upcoming) is None:
            latest = self.latest
            if (
                latest is not None
                and latest.window_end is not None
                # Delivery days are Danish days, whatever HA's time zone is.
                and latest.window_end.astimezone(NEMLIG_TZ).date()
                == now.astimezone(NEMLIG_TZ).date()
            ):
                return OrderStatus.DELIVERED
            return OrderStatus.NONE
        spot = self.upcoming_spot
        if spot is not None and spot.state in SPOT_STATES:
            return SPOT_STATES[spot.state]
        if self.edit_deadline(now) is not None or (spot is None and order.editable):
            return OrderStatus.EDITABLE
        return OrderStatus.PROCESSING


def parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO timestamp from DeliverySpot (UTC, `Z` suffix)."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)


def parse_local(value: str | None) -> datetime | None:
    """Parse a naive order-history timestamp, which is Danish local time."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=NEMLIG_TZ)
