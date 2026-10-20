"""Sensors for Smashrun: last run, history totals and badges."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import SmashrunRun
from .const import RECENT_BADGES
from .coordinator import SmashrunConfigEntry, SmashrunCoordinator, SmashrunData
from .entity import SmashrunEntity
from .runs_util import KM_PER_MILE, format_pace, period_start, totals

StateType = float | int | str | datetime | None


@dataclass(frozen=True, kw_only=True)
class SmashrunSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[SmashrunData, SmashrunCoordinator], StateType]
    period: str | None = None  # week/month/year: sets last_reset
    badges: bool = False  # depends on the (undocumented) badges endpoint


def _last(attr: Callable[[SmashrunRun], Any]) -> Callable[[SmashrunData, Any], Any]:
    return lambda data, _: attr(data.last_run) if data.last_run else None


def _pace_minutes(data: SmashrunData, coordinator: SmashrunCoordinator) -> float | None:
    """Pace in decimal minutes per km (or per mile for imperial users)."""
    pace = data.last_run.pace_min_per_km() if data.last_run else None
    if pace is None:
        return None
    return pace * KM_PER_MILE if coordinator.imperial else pace


def _pace(data: SmashrunData, coordinator: SmashrunCoordinator) -> str | None:
    """Pace as runners read it, "5:52 /km": a number like 5.86 min would mislead."""
    text = format_pace(_pace_minutes(data, coordinator))
    if text is None:
        return None
    return f"{text} /mi" if coordinator.imperial else f"{text} /km"


def _period(period: str, index: int) -> Callable[[SmashrunData, Any], float | int]:
    def value(data: SmashrunData, _: Any) -> float | int:
        return totals(data.runs, period_start(dt_util.now(), period))[index]

    return value


def _distance_in(period: str) -> SmashrunSensorDescription:
    return SmashrunSensorDescription(
        key=f"distance_this_{period}",
        translation_key=f"distance_this_{period}",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        period=period,
        value_fn=_period(period, 0),
    )


def _runs_in(period: str, enabled: bool) -> SmashrunSensorDescription:
    return SmashrunSensorDescription(
        key=f"runs_this_{period}",
        translation_key=f"runs_this_{period}",
        icon="mdi:run",
        native_unit_of_measurement="runs",
        state_class=SensorStateClass.TOTAL,
        entity_registry_enabled_default=enabled,
        period=period,
        value_fn=_period(period, 1),
    )


SENSORS: tuple[SmashrunSensorDescription, ...] = (
    # --- Last run ---------------------------------------------------------
    SmashrunSensorDescription(
        key="last_run_distance",
        translation_key="last_run_distance",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=2,
        value_fn=_last(lambda r: r.distance_km),
    ),
    SmashrunSensorDescription(
        key="last_run_duration",
        translation_key="last_run_duration",
        icon="mdi:timer-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=1,
        value_fn=_last(lambda r: r.duration_s),
    ),
    SmashrunSensorDescription(
        key="last_run_pace",
        translation_key="last_run_pace",
        icon="mdi:speedometer-slow",
        value_fn=_pace,
    ),
    SmashrunSensorDescription(
        key="last_run_speed",
        translation_key="last_run_speed",
        icon="mdi:speedometer",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_last(lambda r: r.speed_kmh()),
    ),
    SmashrunSensorDescription(
        key="last_run_heart_rate_max",
        translation_key="last_run_heart_rate_max",
        icon="mdi:heart-flash",
        native_unit_of_measurement="bpm",
        suggested_display_precision=0,
        value_fn=_last(lambda r: r.value("heartRateMax")),
    ),
    SmashrunSensorDescription(
        key="last_run_elevation_gain",
        translation_key="last_run_elevation_gain",
        icon="mdi:elevation-rise",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_display_precision=0,
        value_fn=_last(lambda r: r.value("elevationGain")),
    ),
    # --- History ----------------------------------------------------------
    SmashrunSensorDescription(
        key="total_distance",
        translation_key="total_distance",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda data, _: totals(data.runs)[0],
    ),
    SmashrunSensorDescription(
        key="total_runs",
        translation_key="total_runs",
        icon="mdi:run",
        native_unit_of_measurement="runs",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data, _: totals(data.runs)[1],
    ),
    _distance_in("week"),
    _distance_in("month"),
    _distance_in("year"),
    _runs_in("week", enabled=False),
    _runs_in("month", enabled=False),
    _runs_in("year", enabled=True),
    # --- Badges -----------------------------------------------------------
    SmashrunSensorDescription(
        key="badges",
        translation_key="badges",
        icon="mdi:medal",
        native_unit_of_measurement="badges",
        state_class=SensorStateClass.MEASUREMENT,
        badges=True,
        value_fn=lambda data, _: len(data.badges),
    ),
    # --- Token ------------------------------------------------------------
    SmashrunSensorDescription(
        key="token_expires",
        translation_key="token_expires",
        icon="mdi:key-chain-variant",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, coordinator: coordinator.token_expires,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmashrunConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Smashrun sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SmashrunSensor(coordinator, desc) for desc in SENSORS)


class SmashrunSensor(SmashrunEntity, SensorEntity):
    """A value derived from the coordinator data."""

    entity_description: SmashrunSensorDescription
    # The badge list is large; keep it out of the DB.
    _unrecorded_attributes = frozenset({"recent_badges"})

    def __init__(
        self, coordinator: SmashrunCoordinator, description: SmashrunSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.badges and not self.coordinator.data.badges_available:
            return False
        return super().available

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data, self.coordinator)

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.period is None:
            return None
        return period_start(dt_util.now(), self.entity_description.period)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        key = self.entity_description.key

        if key == "last_run_pace" and data.last_run is not None:
            minutes = _pace_minutes(data, self.coordinator)
            return {
                "minutes": round(minutes, 3) if minutes is not None else None,
                "unit": "min/mi" if self.coordinator.imperial else "min/km",
            }
        if key == "badges":
            return {
                "recent_badges": [
                    {
                        "name": b.name,
                        "earned": b.earned.isoformat() if b.earned else None,
                        "image": b.image,
                    }
                    for b in data.badges[:RECENT_BADGES]
                ]
            }
        return None
