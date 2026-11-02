"""Delivery sensors for the Nemlig.com integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NemligConfigEntry
from .entity import NemligDeliveryEntity
from .models import DeliveryData, OrderStatus

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NemligSensorDescription(SensorEntityDescription):
    """Describes a delivery sensor."""

    value_fn: Callable[[DeliveryData, datetime], Any]
    attrs_fn: Callable[[DeliveryData], dict[str, Any]] | None = None


def _next_delivery_attrs(data: DeliveryData) -> dict[str, Any]:
    order = data.upcoming
    return {
        "window_end": data.window_end.isoformat() if data.window_end else None,
        "order_number": order.number if order else None,
        "total": order.total if order else None,
    }


def _eta_attrs(data: DeliveryData) -> dict[str, Any]:
    spot = data.upcoming_spot
    return {
        "window_start": spot.eta_start.isoformat() if spot and spot.eta_start else None,
        "window_end": spot.eta_end.isoformat() if spot and spot.eta_end else None,
    }


def _status_attrs(data: DeliveryData) -> dict[str, Any]:
    spot = data.upcoming_spot
    return {"nemlig_state": spot.state if spot else None}


def _progress(data: DeliveryData, now: datetime) -> float | None:
    spot = data.upcoming_spot
    if spot is None or spot.progress is None:
        return None
    return round(spot.progress * 100)


SENSORS: tuple[NemligSensorDescription, ...] = (
    NemligSensorDescription(
        key="next_delivery",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, now: data.window_start,
        attrs_fn=_next_delivery_attrs,
    ),
    NemligSensorDescription(
        key="edit_deadline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, now: data.edit_deadline(now),
    ),
    NemligSensorDescription(
        key="delivery_eta",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, now: (spot := data.upcoming_spot) and spot.eta,
        attrs_fn=_eta_attrs,
    ),
    NemligSensorDescription(
        key="delivery_progress",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_progress,
    ),
    NemligSensorDescription(
        key="order_status",
        device_class=SensorDeviceClass.ENUM,
        options=OrderStatus.OPTIONS,
        value_fn=lambda data, now: data.status(now),
        attrs_fn=_status_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NemligConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the delivery sensors."""
    coordinator = entry.runtime_data.delivery
    async_add_entities(
        NemligSensor(coordinator, description) for description in SENSORS
    )


class NemligSensor(NemligDeliveryEntity, SensorEntity):
    """A delivery sensor."""

    entity_description: NemligSensorDescription

    def __init__(self, coordinator, description: NemligSensorDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
