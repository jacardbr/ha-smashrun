"""Async client for the Smashrun REST API (https://api.smashrun.com/v1).

Auth model: a personal (user-level) access token, obtained by the user through
Smashrun's implicit OAuth flow with client id "client". It is sent as
`Authorization: Bearer <token>`, lasts about 60 days and cannot be refreshed.
User-level tokens are limited to 250 requests per hour.

Only the read endpoints are used:
  - GET /auth/{token}                        token check (expires_in)
  - GET /my/userinfo                         profile, unit preferences
  - GET /my/activities/search/extended       latest run (summary + extended)
  - GET /my/activities/search/briefs         every run, paged (id/start/km/s)
  - GET /my/badges                           earned badges (undocumented shape)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
import logging
from typing import Any, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_URL = "https://api.smashrun.com/v1"

_TIMEOUT = aiohttp.ClientTimeout(total=30)

_PAGE_SIZE = 100
_MAX_PAGES = 500  # hard stop: 50,000 runs


class SmashrunError(Exception):
    """Generic Smashrun API error."""


class SmashrunConnectionError(SmashrunError):
    """Network-level failure talking to Smashrun."""


class SmashrunAuthError(SmashrunError):
    """Token rejected (revoked access, or expired and not refreshed)."""


class SmashrunRateLimitError(SmashrunError):
    """HTTP 429 from Smashrun."""


def _num(value: Any) -> Optional[float]:
    """A number the API may send as a string, or None."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any, tz: Optional[tzinfo]) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp; naive values are taken as local time in `tz`."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python < 3.11 only accepts up to 6 fractional digits.
    if "." in text:
        head, _, rest = text.partition(".")
        digits = ""
        while rest and rest[0].isdigit():
            digits, rest = digits + rest[0], rest[1:]
        text = f"{head}.{digits[:6].ljust(6, '0')}{rest}" if digits else head + rest
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None and tz is not None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


@dataclass
class SmashrunRun:
    """One run. Built from a brief, a summary or an extended summary."""

    run_id: str
    start: Optional[datetime]
    distance_km: Optional[float]
    duration_s: Optional[float]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any], tz: Optional[tzinfo]) -> SmashrunRun:
        # Briefs use runId/startTime; summaries use activityId/startDateTimeLocal.
        run_id = data.get("activityId", data.get("runId"))
        return cls(
            run_id=str(run_id) if run_id is not None else "",
            start=parse_datetime(
                data.get("startDateTimeLocal") or data.get("startTime"), tz
            ),
            distance_km=_num(data.get("distance")),
            duration_s=_num(data.get("duration")),
            raw=data,
        )

    @property
    def end(self) -> Optional[datetime]:
        if self.start is None:
            return None
        return self.start + timedelta(seconds=self.duration_s or 0)

    def pace_min_per_km(self) -> Optional[float]:
        """Average pace in decimal minutes per km."""
        if not self.distance_km or not self.duration_s:
            return None
        return self.duration_s / 60 / self.distance_km

    def speed_kmh(self) -> Optional[float]:
        if not self.distance_km or not self.duration_s:
            return None
        return self.distance_km / (self.duration_s / 3600)

    def value(self, key: str) -> Optional[float]:
        """A numeric field of the raw payload (heartRateAverage, elevationGain...)."""
        return _num(self.raw.get(key))


_NAME_KEYS = ("name", "badgeName", "title")
_DESCRIPTION_KEYS = ("description", "requirement", "requirements")
_IMAGE_KEYS = ("imageLarge", "image", "imageUrl", "imageSmall", "imageTiny")
_DATE_KEYS = ("dateEarnedUTC", "dateEarned", "dateEarnedLocal", "earnedDate", "date")


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


@dataclass
class SmashrunBadge:
    """One earned badge.

    The badges endpoint is listed in the API explorer but its response is not
    documented, so the usual field names are tried in turn and the raw object
    is kept.
    """

    badge_id: Optional[str]
    name: str
    description: Optional[str]
    image: Optional[str]
    earned: Optional[datetime]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any], tz: Optional[tzinfo]) -> SmashrunBadge:
        earned_raw = _first(data, _DATE_KEYS)
        if earned_raw is None:
            # Any other "...date..." field, e.g. a renamed one.
            earned_raw = next(
                (v for k, v in data.items() if "date" in k.lower() and v), None
            )
        badge_id = data.get("id", data.get("badgeId"))
        image = _first(data, _IMAGE_KEYS)
        if image is None:
            image = next(
                (
                    v
                    for k, v in data.items()
                    if k.lower().startswith("image") and isinstance(v, str) and v
                ),
                None,
            )
        return cls(
            badge_id=str(badge_id) if badge_id is not None else None,
            name=str(_first(data, _NAME_KEYS) or f"Badge {badge_id or '?'}"),
            description=_first(data, _DESCRIPTION_KEYS),
            image=image,
            earned=parse_datetime(earned_raw, tz),
            raw=data,
        )


def clean_token(value: str) -> str:
    """Accept a bare token, "Bearer <token>" or a pasted redirect URL/fragment."""
    text = value.strip()
    if "access_token=" in text:
        text = text.split("access_token=", 1)[1].split("&", 1)[0]
    if text.lower().startswith("bearer "):
        text = text[7:]
    return text.strip()


class SmashrunApi:
    """Thin client for one access token."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    async def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        """GET a path under the API root and return the decoded JSON."""
        token = self._token
        url = f"{API_URL}/{path.lstrip('/')}"
        # The token check puts the token in the path; never log it.
        shown = path.replace(token, "<token>") if token else path
        try:
            async with self._session.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise SmashrunAuthError(f"HTTP {resp.status} from {shown}")
                if resp.status == 429:
                    raise SmashrunRateLimitError(f"Rate limited on {shown}")
                if resp.status >= 400:
                    raise SmashrunError(f"HTTP {resp.status} from {shown}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SmashrunConnectionError(str(err)) from err
        except ValueError as err:
            raise SmashrunError(f"Invalid JSON from {shown}") from err

    async def async_get_token_expires_in(self) -> Optional[float]:
        """Seconds until the token expires, as reported by the token check."""
        data = await self._get(f"auth/{self._token}")
        if not isinstance(data, dict):
            return None
        return _num(data.get("expires_in", data.get("expiresIn")))

    async def async_get_userinfo(self) -> dict[str, Any]:
        data = await self._get("my/userinfo")
        if not isinstance(data, dict) or data.get("id") is None:
            raise SmashrunError("Unexpected userinfo response")
        return data

    async def async_get_latest_run(self, tz: Optional[tzinfo]) -> Optional[SmashrunRun]:
        """The most recent run with the extended fields, or None if there are none."""
        try:
            data = await self._get("my/activities/search/extended", {"count": 1})
        except (SmashrunAuthError, SmashrunConnectionError, SmashrunRateLimitError):
            raise
        except SmashrunError as err:
            _LOGGER.debug("Extended search failed (%s); using the plain summary", err)
            data = await self._get("my/activities/search", {"count": 1})
        runs = [
            SmashrunRun.from_json(r, tz) for r in (data or []) if isinstance(r, dict)
        ]
        if not runs:
            return None
        return max(runs, key=lambda r: r.start or datetime.min.replace(tzinfo=tz))

    async def async_get_all_runs(self, tz: Optional[tzinfo]) -> list[SmashrunRun]:
        """Every run of the account (briefs), oldest first.

        Paging stops on an empty page or one that brings nothing new, so it
        works whatever page size the server actually honours.
        """
        found: dict[str, SmashrunRun] = {}
        for page in range(_MAX_PAGES):
            data = await self._get(
                "my/activities/search/briefs", {"page": page, "count": _PAGE_SIZE}
            )
            if not isinstance(data, list) or not data:
                break
            new = 0
            for item in data:
                if not isinstance(item, dict):
                    continue
                run = SmashrunRun.from_json(item, tz)
                if run.run_id and run.run_id not in found:
                    found[run.run_id] = run
                    new += 1
            if new == 0:
                break
        else:
            _LOGGER.warning("Stopped paging Smashrun runs after %d pages", _MAX_PAGES)
        return sorted(
            (r for r in found.values() if r.start is not None),
            key=lambda r: r.start,
        )

    async def async_get_badges(self, tz: Optional[tzinfo]) -> list[SmashrunBadge]:
        """Earned badges, most recent first (undated ones last)."""
        data = await self._get("my/badges")
        if isinstance(data, dict):  # tolerate a wrapped list
            data = next((v for v in data.values() if isinstance(v, list)), [])
        badges = [
            SmashrunBadge.from_json(b, tz) for b in (data or []) if isinstance(b, dict)
        ]
        dated = sorted((b for b in badges if b.earned), key=lambda b: b.earned, reverse=True)
        return dated + [b for b in badges if not b.earned]
