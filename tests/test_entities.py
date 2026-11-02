"""Tests for the delivery entities and coordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.nemlig.const import EVENT_DELIVERED
from custom_components.nemlig.coordinator import DELIVERY_DAY_INTERVAL, IDLE_INTERVAL

from .conftest import load_fixture

# Delivery morning, 08:30 Danish time: window 09-14, ETA 11:41.
pytestmark = pytest.mark.freeze_time("2026-11-02T07:30:00+00:00")


async def test_delivery_entities(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The fixture delivery shows up on every entity."""
    states = {
        entity_id: hass.states.get(entity_id)
        for entity_id in (
            "sensor.nemlig_next_delivery",
            "sensor.nemlig_edit_deadline",
            "sensor.nemlig_expected_arrival",
            "sensor.nemlig_delivery_progress",
            "sensor.nemlig_order_status",
            "binary_sensor.nemlig_delivery_today",
            "calendar.nemlig_deliveries",
        )
    }
    assert all(states.values()), [k for k, v in states.items() if v is None]

    next_delivery = states["sensor.nemlig_next_delivery"]
    assert next_delivery.state == "2026-11-02T08:00:00+00:00"
    assert next_delivery.attributes["window_end"] == "2026-11-02T13:00:00+00:00"
    assert next_delivery.attributes["order_number"] == "2026-12345678"
    assert states["sensor.nemlig_edit_deadline"].state == "unknown"  # passed last night
    eta = states["sensor.nemlig_expected_arrival"]
    assert eta.state == "2026-11-02T10:41:13+00:00"
    assert eta.attributes["window_start"] == "2026-11-02T10:21:13+00:00"
    assert states["sensor.nemlig_delivery_progress"].state == "0"
    status = states["sensor.nemlig_order_status"]
    assert status.state == "ready_for_delivery"
    assert status.attributes["nemlig_state"] == "ReadyForDelivery"
    assert states["binary_sensor.nemlig_delivery_today"].state == "on"
    calendar = states["calendar.nemlig_deliveries"]
    assert calendar.state == "off"  # window opens at 09:00
    assert calendar.attributes["message"] == "Nemlig.com delivery"


async def test_calendar_events(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The calendar lists recent and upcoming deliveries."""
    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.nemlig_deliveries",
            "start_date_time": "2026-10-01T00:00:00+02:00",
            "end_date_time": "2026-11-30T00:00:00+01:00",
        },
        blocking=True,
        return_response=True,
    )
    events = result["calendar.nemlig_deliveries"]["events"]
    assert [e["start"] for e in events] == [
        "2026-10-18T16:00:00+02:00",
        "2026-11-02T09:00:00+01:00",
    ]
    assert "2026-12345678" in events[1]["description"]


async def test_adaptive_interval(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Polling is fast around the delivery window and slow otherwise."""
    coordinator = setup_integration.runtime_data.delivery
    assert coordinator.update_interval == DELIVERY_DAY_INTERVAL

    history = load_fixture("order_history.json")
    for order in history["Orders"]:
        order["Status"] = 3
    mock_api["get_order_history"].return_value = history
    mock_api["get_delivery_spot"].return_value = None
    freezer.tick(DELIVERY_DAY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert coordinator.update_interval == IDLE_INTERVAL


async def test_delivered_event(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """nemlig_delivered fires once when the order turns delivered."""
    events = async_capture_events(hass, EVENT_DELIVERED)
    history = load_fixture("order_history.json")
    history["Orders"][0]["Status"] = 3
    mock_api["get_order_history"].return_value = history

    for _ in range(2):
        freezer.tick(timedelta(minutes=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["order_number"] == "2026-12345678"
    assert hass.states.get("sensor.nemlig_order_status").state == "delivered"
    assert hass.states.get("sensor.nemlig_next_delivery").state == "unknown"


async def test_update_failure_marks_unavailable(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entities go unavailable while nemlig.com cannot be reached."""
    from custom_components.nemlig.api import NemligConnectionError

    mock_api["get_delivery_spot"].side_effect = NemligConnectionError
    freezer.tick(DELIVERY_DAY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.nemlig_order_status").state == "unavailable"


async def test_diagnostics_redacted(
    hass: HomeAssistant, hass_client, setup_integration: MockConfigEntry
) -> None:
    """Diagnostics hide the account."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, setup_integration
    )
    text = str(diagnostics)
    assert "anders@example.com" not in text
    assert "hemmelig" not in text
    assert diagnostics["delivery"]["data"]["spot"]["state"] == "ReadyForDelivery"
