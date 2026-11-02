"""The Nemlig.com integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import NemligAuthError, NemligClient, NemligError
from .const import DOMAIN, EVENT_DELIVERED
from .coordinator import NemligBasketCoordinator, NemligData, NemligDeliveryCoordinator
from .products import ProductIndex
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type NemligConfigEntry = ConfigEntry[NemligData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once for all accounts."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NemligConfigEntry) -> bool:
    """Set up Nemlig.com from a config entry."""
    # A dedicated session: login cookies must not mix with HA's shared session.
    # Created during entry setup, HA detaches it when the entry unloads.
    session = async_create_clientsession(hass)
    client = NemligClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    try:
        await client.async_login()
    except NemligAuthError as err:
        raise ConfigEntryAuthFailed("nemlig.com rejected the login") from err
    except NemligError as err:
        raise ConfigEntryNotReady(f"Could not log in to nemlig.com: {err}") from err

    delivery = NemligDeliveryCoordinator(hass, entry, client)
    await delivery.async_config_entry_first_refresh()
    basket = NemligBasketCoordinator(hass, entry, client)
    await basket.async_config_entry_first_refresh()
    products = ProductIndex(hass, entry.entry_id, client)
    await products.async_load()

    entry.runtime_data = NemligData(
        client=client, delivery=delivery, basket=basket, products=products
    )

    async def refresh_products(order_id: int | None = None) -> None:
        # DeliverySpot knows about a delivery hours before the order history does.
        delivered_ids = {order_id} if order_id else set()
        if data := delivery.data:
            delivered_ids |= {o.id for o in data.orders if data.delivered(o)}
        try:
            await products.async_refresh(delivered_ids)
        except NemligError as err:
            _LOGGER.warning("Could not update the product index: %s", err)

    @callback
    def on_delivered(event: Event) -> None:
        if event.data.get("config_entry_id") == entry.entry_id:
            entry.async_create_background_task(
                hass,
                refresh_products(event.data.get("order_id")),
                "nemlig product index",
            )

    entry.async_on_unload(hass.bus.async_listen(EVENT_DELIVERED, on_delivered))
    if products.stale:
        entry.async_create_background_task(
            hass, refresh_products(), "nemlig product index"
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NemligConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
