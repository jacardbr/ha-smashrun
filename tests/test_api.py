"""Logic tests for the Smashrun API client and run helpers, with a fake HTTP session.

Run from the repo root with the standard library only:

    python3 -m unittest discover -s tests -v

If aiohttp is not installed, a tiny stub is injected so api.py can be imported.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest

# --- aiohttp stub (only when the real one is unavailable) -------------------
if importlib.util.find_spec("aiohttp") is None:
    stub = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    class ClientSession:  # only used as a type annotation
        pass

    stub.ClientError = ClientError
    stub.ClientTimeout = ClientTimeout
    stub.ClientSession = ClientSession
    sys.modules["aiohttp"] = stub

import aiohttp  # noqa: E402

_PKG = Path(__file__).parent.parent / "custom_components" / "ha_smashrun"


def _load(name: str, file: str):
    # Load modules directly so the package __init__ (which needs Home
    # Assistant) is not imported.
    spec = importlib.util.spec_from_file_location(name, _PKG / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = _load("smashrun_api", "api.py")
runs_util = _load("smashrun_runs_util", "runs_util.py")

TZ = timezone(timedelta(hours=-3))


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    """Routes GETs to a handler(path, params) -> (status, body)."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.replace(api.API_URL + "/", "")
        self.calls.append((path, dict(params or {}), dict(headers or {})))
        return _Resp(*self.handler(path, params or {}))


def _client(handler):
    session = FakeSession(handler)
    return api.SmashrunApi(session, "s3cret"), session


def run(coro):
    return asyncio.run(coro)


def _brief(i, day):
    return {
        "runId": 1000 + i,
        "startTime": f"2026-10-{day:02d}T07:00:00-03:00",
        "distance": 5.0 + i,
        "duration": 1800 + 60 * i,
    }


class ParseTests(unittest.TestCase):
    def test_parse_datetime_variants(self):
        self.assertEqual(
            api.parse_datetime("2026-10-01T07:00:00Z", TZ),
            datetime(2026, 10, 1, 7, tzinfo=timezone.utc),
        )
        naive = api.parse_datetime("2026-10-01T07:00:00", TZ)
        self.assertEqual(naive.tzinfo, TZ)
        # More than 6 fractional digits (.NET style) is truncated, not rejected.
        frac = api.parse_datetime("2026-10-01T07:00:00.1234567-03:00", TZ)
        self.assertEqual(frac.microsecond, 123456)
        self.assertIsNone(api.parse_datetime("garbage", TZ))
        self.assertIsNone(api.parse_datetime(None, TZ))

    def test_clean_token(self):
        self.assertEqual(api.clean_token("  abc123 \n"), "abc123")
        self.assertEqual(api.clean_token("Bearer abc123"), "abc123")
        self.assertEqual(
            api.clean_token(
                "urn:ietf:wg:oauth:2.0:oob#access_token=abc123&token_type=bearer&expires_in=5183999"
            ),
            "abc123",
        )

    def test_run_from_summary_and_brief(self):
        summary = api.SmashrunRun.from_json(
            {
                "activityId": 42,
                "startDateTimeLocal": "2026-10-01T07:00:00-03:00",
                "distance": 10,
                "duration": "3000",
                "heartRateAverage": 150,
            },
            TZ,
        )
        self.assertEqual(summary.run_id, "42")
        self.assertEqual(summary.duration_s, 3000.0)
        self.assertAlmostEqual(summary.pace_min_per_km(), 5.0)
        self.assertAlmostEqual(summary.speed_kmh(), 12.0)
        self.assertEqual(summary.value("heartRateAverage"), 150.0)
        self.assertIsNone(summary.value("missing"))
        self.assertEqual(summary.end - summary.start, timedelta(seconds=3000))

        brief = api.SmashrunRun.from_json(_brief(0, 2), TZ)
        self.assertEqual(brief.run_id, "1000")
        self.assertEqual(brief.distance_km, 5.0)

    def test_pace_without_distance(self):
        run_ = api.SmashrunRun.from_json({"activityId": 1, "distance": 0, "duration": 60}, TZ)
        self.assertIsNone(run_.pace_min_per_km())
        self.assertIsNone(run_.speed_kmh())

    def test_badge_field_fallbacks(self):
        badge = api.SmashrunBadge.from_json(
            {
                "id": 7,
                "name": "Early bird",
                "requirement": "Run before 6am",
                "imageSmall": "https://x/s.png",
                "imageLarge": "https://x/l.png",
                "dateEarnedUTC": "2026-01-02T03:04:05Z",
            },
            TZ,
        )
        self.assertEqual(badge.badge_id, "7")
        self.assertEqual(badge.description, "Run before 6am")
        self.assertEqual(badge.image, "https://x/l.png")
        self.assertEqual(badge.earned.year, 2026)

        odd = api.SmashrunBadge.from_json(
            {"badgeId": 9, "imageThumb": "https://x/t.png", "whenDateGot": "2026-02-01"},
            TZ,
        )
        self.assertEqual(odd.name, "Badge 9")
        self.assertEqual(odd.image, "https://x/t.png")
        self.assertEqual(odd.earned.month, 2)


class ClientTests(unittest.TestCase):
    def test_sends_bearer_token(self):
        client, session = _client(lambda p, q: (200, {"id": 5, "username": "me"}))
        user = run(client.async_get_userinfo())
        self.assertEqual(user["username"], "me")
        self.assertEqual(session.calls[0][2]["Authorization"], "Bearer s3cret")

    def test_error_mapping(self):
        for status, exc in (
            (401, api.SmashrunAuthError),
            (403, api.SmashrunAuthError),
            (429, api.SmashrunRateLimitError),
            (500, api.SmashrunError),
        ):
            client, _ = _client(lambda p, q, s=status: (s, None))
            with self.assertRaises(exc):
                run(client.async_get_userinfo())

        client, _ = _client(lambda p, q: (200, ValueError("bad json")))
        with self.assertRaises(api.SmashrunError):
            run(client.async_get_userinfo())

        def boom(p, q):
            raise aiohttp.ClientError("down")

        client, _ = _client(boom)
        with self.assertRaises(api.SmashrunConnectionError):
            run(client.async_get_userinfo())

    def test_token_expiry_and_redaction(self):
        client, session = _client(lambda p, q: (200, {"expires_in": 3600, "token": "tok"}))
        self.assertEqual(run(client.async_get_token_expires_in()), 3600.0)
        self.assertEqual(session.calls[0][0], "auth/s3cret")

        client, _ = _client(lambda p, q: (500, None))
        with self.assertRaises(api.SmashrunError) as ctx:
            run(client.async_get_token_expires_in())
        self.assertNotIn("s3cret", str(ctx.exception))
        self.assertIn("<token>", str(ctx.exception))

    def test_latest_run_falls_back_to_plain_search(self):
        def handler(path, params):
            if path.endswith("/extended"):
                return 404, None
            return 200, [
                {"activityId": 1, "startDateTimeLocal": "2026-10-01T07:00:00", "distance": 5, "duration": 1500},
                {"activityId": 2, "startDateTimeLocal": "2026-10-03T07:00:00", "distance": 8, "duration": 2400},
            ]

        client, session = _client(handler)
        latest = run(client.async_get_latest_run(TZ))
        self.assertEqual(latest.run_id, "2")
        self.assertEqual([c[0] for c in session.calls][-1], "my/activities/search")

    def test_latest_run_auth_error_is_not_swallowed(self):
        client, _ = _client(lambda p, q: (401, None))
        with self.assertRaises(api.SmashrunAuthError):
            run(client.async_get_latest_run(TZ))

    def test_latest_run_none_when_empty(self):
        client, _ = _client(lambda p, q: (200, []))
        self.assertIsNone(run(client.async_get_latest_run(TZ)))

    def test_all_runs_pages_until_empty(self):
        pages = {0: [_brief(i, 10 - i) for i in range(3)], 1: [_brief(3, 1)]}

        client, session = _client(lambda p, q: (200, pages.get(q["page"], [])))
        runs = run(client.async_get_all_runs(TZ))
        self.assertEqual([r.run_id for r in runs], ["1003", "1002", "1001", "1000"])
        self.assertEqual(len(session.calls), 3)

    def test_all_runs_stops_when_server_ignores_page(self):
        same = [_brief(0, 1), _brief(1, 2)]
        client, session = _client(lambda p, q: (200, same))
        runs = run(client.async_get_all_runs(TZ))
        self.assertEqual(len(runs), 2)
        self.assertEqual(len(session.calls), 2)

    def test_badges_sorted_and_unwrapped(self):
        body = {
            "badges": [
                {"id": 1, "name": "Old", "dateEarned": "2025-01-01T00:00:00"},
                {"id": 2, "name": "Undated"},
                {"id": 3, "name": "New", "dateEarned": "2026-05-01T00:00:00"},
            ]
        }
        client, _ = _client(lambda p, q: (200, body))
        badges = run(client.async_get_badges(TZ))
        self.assertEqual([b.name for b in badges], ["New", "Old", "Undated"])


class RunsUtilTests(unittest.TestCase):
    def _runs(self):
        return [
            api.SmashrunRun.from_json(
                {"runId": 1, "startTime": "2026-10-12T07:10:00-03:00", "distance": 5, "duration": 1500}, TZ
            ),
            api.SmashrunRun.from_json(
                {"runId": 2, "startTime": "2026-10-12T07:50:00-03:00", "distance": 2, "duration": 600}, TZ
            ),
            api.SmashrunRun.from_json(
                {"runId": 3, "startTime": "2026-10-19T06:00:00-03:00", "distance": 10, "duration": 3000}, TZ
            ),
            api.SmashrunRun.from_json(
                {"runId": 4, "startTime": "2025-12-31T06:00:00-03:00", "distance": 21.1, "duration": 6300}, TZ
            ),
        ]

    def test_period_start(self):
        now = datetime(2026, 10, 21, 15, 30, tzinfo=TZ)  # a Wednesday
        self.assertEqual(runs_util.period_start(now, "week"), datetime(2026, 10, 19, tzinfo=TZ))
        self.assertEqual(runs_util.period_start(now, "month"), datetime(2026, 10, 1, tzinfo=TZ))
        self.assertEqual(runs_util.period_start(now, "year"), datetime(2026, 1, 1, tzinfo=TZ))
        with self.assertRaises(ValueError):
            runs_util.period_start(now, "decade")

    def test_totals(self):
        runs = self._runs()
        km, n = runs_util.totals(runs)
        self.assertAlmostEqual(km, 38.1)
        self.assertEqual(n, 4)
        km, n = runs_util.totals(runs, datetime(2026, 10, 19, tzinfo=TZ))
        self.assertEqual((km, n), (10.0, 1))

    def test_hourly_cumulative_merges_same_hour(self):
        rows = runs_util.hourly_cumulative(self._runs())
        self.assertEqual(len(rows), 3)  # runs 1 and 2 share 10:00 UTC
        self.assertEqual(rows[0], (datetime(2025, 12, 31, 9, tzinfo=timezone.utc), 21.1, 1))
        self.assertEqual(rows[1], (datetime(2026, 10, 12, 10, tzinfo=timezone.utc), 28.1, 3))
        self.assertEqual(rows[2][1:], (38.1, 4))

    def test_sensor_statistics_totals(self):
        cutoff = datetime(2026, 10, 20, 0, tzinfo=timezone.utc)
        rows = runs_util.sensor_statistics(
            self._runs(), count=False, period=None, cutoff=cutoff, anchor_sum=None, tz=TZ
        )
        # One row per hour with a run; state and sum are the running total.
        self.assertEqual(
            [(r[0].isoformat(), r[1], r[2], r[3]) for r in rows],
            [
                ("2025-12-31T09:00:00+00:00", 21.1, 21.1, None),
                ("2026-10-12T10:00:00+00:00", 28.1, 28.1, None),
                ("2026-10-19T09:00:00+00:00", 38.1, 38.1, None),
            ],
        )
        counts = runs_util.sensor_statistics(
            self._runs(), count=True, period=None, cutoff=cutoff, anchor_sum=None, tz=TZ
        )
        self.assertEqual([r[1] for r in counts], [1, 3, 4])

    def test_sensor_statistics_only_before_cutoff_and_anchored(self):
        # The recorder already has a row at 2026-10-19 09:00 UTC (the hour of
        # run 3) with sum 1000: imported sums must end so that the change
        # into that row is exactly run 3's 10 km.
        cutoff = datetime(2026, 10, 19, 9, tzinfo=timezone.utc)
        rows = runs_util.sensor_statistics(
            self._runs(), count=False, period=None, cutoff=cutoff, anchor_sum=1000.0, tz=TZ
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r[0] < cutoff for r in rows))
        self.assertAlmostEqual(rows[-1][2], 990.0)
        self.assertAlmostEqual(rows[-1][2] - rows[0][2], 7.0)
        self.assertEqual(rows[-1][1], 28.1)  # state is not shifted

    def test_sensor_statistics_periods_reset(self):
        cutoff = datetime(2026, 10, 20, 0, tzinfo=timezone.utc)
        rows = runs_util.sensor_statistics(
            self._runs(), count=False, period="week", cutoff=cutoff, anchor_sum=None, tz=TZ
        )
        by_hour = {r[0]: r for r in rows}
        # Run 4 (Wed 2025-12-31) alone in its week.
        r4 = by_hour[datetime(2025, 12, 31, 9, tzinfo=timezone.utc)]
        self.assertEqual(r4[1], 21.1)
        self.assertEqual(r4[3], datetime(2025, 12, 29, tzinfo=TZ))
        # Monday 2026-10-12 local midnight = 03:00 UTC: a new week starts at 0.
        monday = by_hour[datetime(2026, 10, 12, 3, tzinfo=timezone.utc)]
        self.assertEqual(monday[1], 0.0)
        self.assertEqual(monday[3], datetime(2026, 10, 12, tzinfo=TZ))
        # Runs 1 and 2 later that Monday add up within the week.
        self.assertEqual(by_hour[datetime(2026, 10, 12, 10, tzinfo=timezone.utc)][1], 7.0)
        # Every week between the first run and the cutoff has a boundary row.
        boundaries = [r for r in rows if r[0] == runs_util._utc_hour(r[3])]
        self.assertEqual(len(boundaries), 43)
        # The sum never decreases: it is the all-time distance.
        sums = [r[2] for r in rows]
        self.assertEqual(sums, sorted(sums))
        self.assertEqual(sums[-1], 38.1)

        years = runs_util.sensor_statistics(
            self._runs(), count=True, period="year", cutoff=cutoff, anchor_sum=None, tz=TZ
        )
        self.assertEqual(
            [(r[0].year, r[1]) for r in years],
            [(2025, 0.0), (2025, 1.0), (2026, 0.0), (2026, 2.0), (2026, 3.0)],
        )

    def test_sensor_statistics_empty(self):
        cutoff = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.assertEqual(
            runs_util.sensor_statistics([], count=False, period="week", cutoff=cutoff, anchor_sum=None, tz=TZ),
            [],
        )

    def test_yearly_summary(self):
        years = runs_util.yearly_summary(self._runs(), TZ)
        self.assertEqual(list(years), ["2025", "2026"])
        self.assertEqual(years["2025"]["runs"], 1)
        self.assertEqual(years["2025"]["months_km"][11], 21.1)
        self.assertEqual(years["2026"]["months_km"][9], 17.0)
        self.assertEqual(sum(years["2026"]["months_km"]), 17.0)

    def test_yearly_summary_uses_local_year(self):
        # 2026-01-01 01:00 UTC is still New Year's Eve 2025 at UTC-3.
        run_ = api.SmashrunRun.from_json(
            {"runId": 9, "startTime": "2026-01-01T01:00:00Z", "distance": 3, "duration": 900}, TZ
        )
        self.assertEqual(list(runs_util.yearly_summary([run_], TZ)), ["2025"])

    def test_personal_records(self):
        runs = self._runs()
        runs.append(
            api.SmashrunRun.from_json(  # a fast 4.96 km run: counts as a 5K (GPS)
                {"runId": 5, "startTime": "2026-03-01T07:00:00-03:00", "distance": 4.96, "duration": 1240}, TZ
            )
        )
        records = runs_util.personal_records(runs, TZ)
        five = records["all"]["5k"]
        self.assertEqual(five["run_id"], "5")
        self.assertEqual(five["pace_s_per_km"], 250.0)
        self.assertEqual(five["time_s"], 1250.0)
        # 10K: the half marathon (21.1 km in 1:45:00, 4:58.6/km) beats run 3
        # (10 km in 50:00): a longer run can hold a shorter record.
        self.assertEqual(records["all"]["10k"]["run_id"], "4")
        self.assertAlmostEqual(records["all"]["10k"]["time_s"], 6300 / 21.1 * 10, places=0)
        # Within 2026 only, run 3 is the best 10K.
        self.assertEqual(records["2026"]["10k"]["run_id"], "3")
        self.assertEqual(records["2026"]["10k"]["time_s"], 3000.0)
        # 21K: only run 4 is long enough; 42K: nobody.
        self.assertEqual(records["all"]["21k"]["run_id"], "4")
        self.assertAlmostEqual(records["all"]["21k"]["time_s"], 6300 / 21.1 * 21.0975, places=0)
        self.assertIsNone(records["all"]["42k"])
        # Per year.
        self.assertEqual(records["2025"]["5k"]["run_id"], "4")
        self.assertIsNone(records["2026"]["21k"])
        self.assertEqual(records["2026"]["5k"]["run_id"], "5")

    def test_personal_records_empty(self):
        self.assertEqual(
            runs_util.personal_records([], TZ),
            {"all": {"5k": None, "10k": None, "21k": None, "42k": None}},
        )

    def test_signature_changes_with_edits(self):
        runs = self._runs()
        before = runs_util.runs_signature(runs)
        runs[0].distance_km = 6
        self.assertNotEqual(before, runs_util.runs_signature(runs))

    def test_formatting(self):
        self.assertEqual(runs_util.format_duration(3725), "1:02:05")
        self.assertEqual(runs_util.format_duration(325), "5:25")
        self.assertEqual(runs_util.format_pace(5.5), "5:30")
        self.assertIsNone(runs_util.format_pace(None))


if __name__ == "__main__":
    unittest.main()
