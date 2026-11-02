"""Base entity for the Nemlig.com integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NemligBasketCoordinator, NemligDeliveryCoordinator


class NemligEntity[CoordinatorT: (NemligDeliveryCoordinator, NemligBasketCoordinator)](
    CoordinatorEntity[CoordinatorT]
):
    """An entity on the account's Nemlig device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CoordinatorT, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        account = coordinator.config_entry.unique_id
        self._attr_unique_id = f"{account}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(account))},
            name="Nemlig",
            manufacturer="Nemlig.com",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.nemlig.com",
        )
