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
from .runs_util import KM_PER_MILE, format_duration, format_pace, period_start, totals

StateType = float | int | str | datetime | None


@dataclass(frozen=True, kw_only=True)
class SmashrunSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[SmashrunData, SmashrunCoordinator], StateType]
    period: str | None = None  # week/month/year: sets last_reset
    badges: bool = False  # depends on the (undocumented) badges endpoint


def _last(attr: Callable[[SmashrunRun], Any]) -> Callable[[SmashrunData, Any], Any]:
    return lambda data, _: attr(data.last_run) if data.last_run else None


def _pace(data: SmashrunData, coordinator: SmashrunCoordinator) -> float | None:
    pace = data.last_run.pace_min_per_km() if data.last_run else None
    if pace is None:
        return None
    return pace * KM_PER_MILE if coordinator.imperial else pace


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
        key="last_run",
        translation_key="last_run",
        icon="mdi:run-fast",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last(lambda r: r.start),
    ),
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
        suggested_display_precision=2,
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
        key="last_run_heart_rate_average",
        translation_key="last_run_heart_rate_average",
        icon="mdi:heart-pulse",
        native_unit_of_measurement="bpm",
        suggested_display_precision=0,
        value_fn=_last(lambda r: r.value("heartRateAverage")),
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
        key="last_run_cadence",
        translation_key="last_run_cadence",
        icon="mdi:shoe-print",
        native_unit_of_measurement="spm",
        suggested_display_precision=0,
        value_fn=_last(lambda r: r.value("cadenceAverage")),
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
    SmashrunSensorDescription(
        key="latest_badge",
        translation_key="latest_badge",
        icon="mdi:medal-outline",
        badges=True,
        value_fn=lambda data, _: data.badges[0].name if data.badges else None,
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
    # The raw run payload and the badge list are large; keep them out of the DB.
    _unrecorded_attributes = frozenset({"raw", "recent_badges"})

    def __init__(
        self, coordinator: SmashrunCoordinator, description: SmashrunSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.key == "last_run_pace":
            self._attr_native_unit_of_measurement = (
                "min/mi" if coordinator.imperial else "min/km"
            )

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
    def entity_picture(self) -> str | None:
        if self.entity_description.key == "latest_badge" and self.coordinator.data.badges:
            return self.coordinator.data.badges[0].image
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        key = self.entity_description.key

        if key == "last_run" and data.last_run is not None:
            run = data.last_run
            pace = run.pace_min_per_km()
            return {
                "run_id": run.run_id,
                "url": self.run_url(run.run_id),
                "distance_km": run.distance_km,
                "duration": format_duration(run.duration_s),
                "pace_per_km": format_pace(pace),
                "pace_per_mile": format_pace(pace * KM_PER_MILE if pace else None),
                "end": run.end.isoformat() if run.end else None,
                "raw": run.raw,
            }
        if key == "last_run_pace" and data.last_run is not None:
            return {"pace": format_pace(self.native_value)}
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
        if key == "latest_badge" and data.badges:
            badge = data.badges[0]
            return {
                "earned": badge.earned.isoformat() if badge.earned else None,
                "description": badge.description,
                "image": badge.image,
            }
        return None
