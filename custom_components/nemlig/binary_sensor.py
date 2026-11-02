"""Delivery binary sensor for the Nemlig.com integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NemligConfigEntry
from .coordinator import NemligDeliveryCoordinator
from .entity import NemligEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NemligConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the delivery-today binary sensor."""
    async_add_entities(
        [NemligDeliveryTodaySensor(entry.runtime_data.delivery, "delivery_today")]
    )


class NemligDeliveryTodaySensor(
    NemligEntity[NemligDeliveryCoordinator], BinarySensorEntity
):
    """On when an order's delivery window is today, also after it has arrived."""

    @property
    def is_on(self) -> bool:
        """Return whether a delivery is scheduled for today."""
        today = dt_util.now().date()
        return any(
            order.window_start is not None
            and dt_util.as_local(order.window_start).date() == today
            for order in self.coordinator.data.orders
        )
