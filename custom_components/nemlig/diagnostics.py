"""Diagnostics for the Nemlig.com integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import NemligConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NemligConfigEntry
) -> dict[str, Any]:
    """Return diagnostics; order data holds no names or addresses."""
    delivery = entry.runtime_data.delivery
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "delivery": {
            "last_update_success": delivery.last_update_success,
            "update_interval": str(delivery.update_interval),
            "data": asdict(delivery.data) if delivery.data else None,
        },
    }
