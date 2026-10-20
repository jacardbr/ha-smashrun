"""Calendar with every Smashrun run."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SmashrunRun
from .coordinator import SmashrunConfigEntry, SmashrunCoordinator
from .entity import SmashrunEntity
from .runs_util import KM_PER_MILE, format_duration, format_pace


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmashrunConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SmashrunCalendar(entry.runtime_data)])


class SmashrunCalendar(SmashrunEntity, CalendarEntity):
    """One timed event per run, from its start to start + duration."""

    _attr_translation_key = "runs"

    def __init__(self, coordinator: SmashrunCoordinator) -> None:
        super().__init__(coordinator, "calendar")

    def _event(self, run: SmashrunRun) -> CalendarEvent:
        km = run.distance_km or 0.0
        pace = run.pace_min_per_km()
        if self.coordinator.imperial:
            distance = f"{km / KM_PER_MILE:.2f} mi"
            pace_text = format_pace(pace * KM_PER_MILE if pace else None)
            pace_unit = "/mi"
        else:
            distance = f"{km:.2f} km"
            pace_text = format_pace(pace)
            pace_unit = "/km"
        lines = [f"{distance} in {format_duration(run.duration_s) or '?'}"]
        if pace_text:
            lines.append(f"Pace {pace_text}{pace_unit}")
        if url := self.run_url(run.run_id):
            lines.append(url)
        return CalendarEvent(
            start=run.start,
            # Calendar events must end after they start.
            end=max(run.end, run.start + timedelta(minutes=1)),
            summary=f"Run {distance}",
            description="\n".join(lines),
            uid=f"smashrun-{run.run_id}",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """The most recent run (the calendar has no future events)."""
        runs = self.coordinator.data.runs
        return self._event(runs[-1]) if runs else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            self._event(run)
            for run in self.coordinator.data.runs
            if run.start < end_date and run.end > start_date
        ]
