"""The Nemlig.com integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import NemligAuthError, NemligClient, NemligError
from .coordinator import NemligData, NemligDeliveryCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]

type NemligConfigEntry = ConfigEntry[NemligData]


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

    entry.runtime_data = NemligData(client=client, delivery=delivery)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NemligConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
