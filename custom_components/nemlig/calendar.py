"""Delivery calendar for the Nemlig.com integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NemligConfigEntry
from .entity import NemligDeliveryEntity
from .models import DeliveryData, Order

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NemligConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the delivery calendar."""
    async_add_entities([NemligCalendar(entry.runtime_data.delivery, "deliveries")])


def _events(data: DeliveryData) -> list[CalendarEvent]:
    """One event per order in the recent history, oldest first."""
    upcoming = data.upcoming
    events = []
    for order in reversed(data.orders):
        start, end = _window(data, order, upcoming)
        if start is None or end is None:
            continue
        details = [f"Order {order.number}"] if order.number else []
        if order.total is not None:
            details.append(f"{order.total:.2f} kr.")
        events.append(
            CalendarEvent(
                start=dt_util.as_local(start),
                end=dt_util.as_local(end),
                summary="Nemlig.com delivery",
                description="\n".join(details) or None,
                uid=str(order.id),
            )
        )
    return events


def _window(
    data: DeliveryData, order: Order, upcoming: Order | None
) -> tuple[datetime | None, datetime | None]:
    if upcoming is not None and order.id == upcoming.id:
        return data.window_start, data.window_end
    return order.window_start, order.window_end


class NemligCalendar(NemligDeliveryEntity, CalendarEntity):
    """Recent and upcoming deliveries."""

    @property
    def event(self) -> CalendarEvent | None:
        """The current delivery, or the next one."""
        now = dt_util.utcnow()
        return next(
            (e for e in _events(self.coordinator.data) if e.end_datetime_local > now),
            None,
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Deliveries overlapping the requested range."""
        return [
            event
            for event in _events(self.coordinator.data)
            if event.start_datetime_local < end_date
            and event.end_datetime_local > start_date
        ]
