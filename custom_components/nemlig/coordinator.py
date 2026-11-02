"""Data update coordinators for the Nemlig.com integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import NemligAuthError, NemligClient, NemligError
from .const import DOMAIN, EVENT_DELIVERED
from .models import Basket, DeliveryData, DeliverySpot, Order, OrderStatus
from .products import ProductIndex

if TYPE_CHECKING:
    from . import NemligConfigEntry

_LOGGER = logging.getLogger(__name__)

IDLE_INTERVAL = timedelta(minutes=30)
DELIVERY_DAY_INTERVAL = timedelta(minutes=5)
# Start polling often this long before the delivery window opens.
DELIVERY_LEAD_TIME = timedelta(hours=1)
ORDER_HISTORY_SIZE = 5
BASKET_INTERVAL = timedelta(minutes=10)


@dataclass
class NemligData:
    """What a loaded config entry holds."""

    client: NemligClient
    delivery: NemligDeliveryCoordinator
    basket: NemligBasketCoordinator
    products: ProductIndex


class NemligBasketCoordinator(DataUpdateCoordinator[Basket]):
    """Polls the live basket. Actions that change it push the new basket in directly."""

    config_entry: NemligConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: NemligConfigEntry, client: NemligClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} basket",
            update_interval=BASKET_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> Basket:
        try:
            return Basket.from_api(await self.client.get_basket())
        except NemligAuthError as err:
            raise ConfigEntryAuthFailed("nemlig.com rejected the login") from err
        except NemligError as err:
            raise UpdateFailed(f"Could not update the basket: {err}") from err
        except (KeyError, TypeError, ValueError) as err:
            raise UpdateFailed(
                f"Unexpected basket data from nemlig.com: {err}"
            ) from err


class NemligDeliveryCoordinator(DataUpdateCoordinator[DeliveryData]):
    """Polls the upcoming delivery: every 30 min, every 5 min around delivery time."""

    config_entry: NemligConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: NemligConfigEntry, client: NemligClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} delivery",
            update_interval=IDLE_INTERVAL,
        )
        self.client = client
        self._last_status: str | None = None

    async def _async_update_data(self) -> DeliveryData:
        try:
            spot_raw = await self.client.get_delivery_spot()
            history = await self.client.get_order_history(take=ORDER_HISTORY_SIZE)
        except NemligAuthError as err:
            raise ConfigEntryAuthFailed("nemlig.com rejected the login") from err
        except NemligError as err:
            raise UpdateFailed(f"Could not update delivery data: {err}") from err

        try:
            data = DeliveryData(
                orders=[Order.from_api(o) for o in (history or {}).get("Orders") or []],
                spot=DeliverySpot.from_api(spot_raw) if spot_raw else None,
            )
        except (KeyError, TypeError, ValueError) as err:
            raise UpdateFailed(
                f"Unexpected delivery data from nemlig.com: {err}"
            ) from err

        now = dt_util.utcnow()
        self.update_interval = self._interval(data, now)
        self._fire_delivered(data, now)
        return data

    @staticmethod
    def _interval(data: DeliveryData, now) -> timedelta:
        start, end = data.window_start, data.window_end
        if start is None or end is None:
            return IDLE_INTERVAL
        if start - DELIVERY_LEAD_TIME <= now <= end + DELIVERY_LEAD_TIME:
            return DELIVERY_DAY_INTERVAL
        return IDLE_INTERVAL

    def _fire_delivered(self, data: DeliveryData, now) -> None:
        """Fire nemlig_delivered once when the status turns delivered."""
        status = data.status(now)
        if (
            status == OrderStatus.DELIVERED
            and self._last_status is not None
            and self._last_status != OrderStatus.DELIVERED
            and (order := data.latest) is not None
        ):
            self.hass.bus.async_fire(
                EVENT_DELIVERED,
                {
                    "config_entry_id": self.config_entry.entry_id,
                    "order_id": order.id,
                    "order_number": order.number,
                    "total": order.total,
                },
            )
        self._last_status = status
