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

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, dict(json or {}), dict(headers or {})))
        return _Resp(*self.handler(url, json or {}))


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


class LoginTests(unittest.TestCase):
    """Email/password login through Smashrun's authorization page service."""

    OK = {
        "info": {"isError": False, "isFailed": False, "message": None},
        "results": {"auth": {"access_token": " new-token ", "expires_in": 5184000}},
    }

    def test_login_ok(self):
        session = FakeSession(lambda url, body: (200, self.OK))
        token, expires_in = run(api.async_login(session, " me@example.com ", "pw"))
        self.assertEqual(token, "new-token")
        self.assertEqual(expires_in, 5184000)
        url, body, _ = session.calls[0]
        self.assertEqual(url, api.LOGIN_URL)
        self.assertEqual(
            body,
            {
                "loginEmail": "me@example.com",
                "loginPassword": "pw",
                "clientId": "client",
                "scopes": "read_activity",
                "isToken": True,
            },
        )

    def test_login_wrapped_in_d(self):
        token, expires_in = api.parse_login_response({"d": self.OK})
        self.assertEqual((token, expires_in), ("new-token", 5184000))

    def test_login_refused(self):
        # The real answer for an unknown email (seen with a made-up address).
        refused = {
            "info": {
                "isError": False,
                "isFailed": True,
                "failCode": 1,
                "message": "No user is registered with this email address",
            },
            "results": {},
        }
        session = FakeSession(lambda url, body: (200, refused))
        with self.assertRaises(api.SmashrunLoginError) as ctx:
            run(api.async_login(session, "nobody@example.com", "x"))
        self.assertIn("No user is registered", str(ctx.exception))

    def test_login_without_token(self):
        with self.assertRaises(api.SmashrunError) as ctx:
            api.parse_login_response({"info": {"isFailed": False}, "results": {}})
        self.assertNotIsInstance(ctx.exception, api.SmashrunLoginError)
        with self.assertRaises(api.SmashrunError):
            api.parse_login_response("not json")

    def test_login_http_error(self):
        session = FakeSession(lambda url, body: (500, {}))
        with self.assertRaises(api.SmashrunError):
            run(api.async_login(session, "me@example.com", "pw"))

    def test_set_token(self):
        client, session = _client(lambda path, params: (200, {"id": 1}))
        client.set_token("other")
        run(client.async_get_userinfo())
        self.assertEqual(session.calls[-1][2]["Authorization"], "Bearer other")


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
        # 2 km, 5 km and 10 km in 2026; 21.1 km in 2025.
        self.assertEqual(years["2026"]["counts"], {"lt5k": 1, "5k": 1, "10k": 1, "21k": 0, "42k": 0})
        self.assertEqual(years["2025"]["counts"], {"lt5k": 0, "5k": 0, "10k": 0, "21k": 1, "42k": 0})

    def test_count_buckets(self):
        self.assertEqual(runs_util.count_buckets([4.5, 9.5, 20.5, 41.5, 48]), runs_util.COUNT_BUCKETS)
        custom = runs_util.count_buckets([4, 8, 18, 40, 60])
        self.assertEqual(custom["lt5k"], (0.0, 4.0))
        self.assertEqual(custom["42k"], (40.0, 60.0))
        self.assertEqual(runs_util.distance_bucket(8.5, custom), "10k")
        self.assertEqual(runs_util.distance_bucket(50, custom), "42k")
        for bad in ([4, 9, 20, 41], [5, 4, 20, 41, 48], [0, 9, 20, 41, 48], [4, 9, 20, 41, 41]):
            with self.assertRaises(ValueError):
                runs_util.count_buckets(bad)

    def test_yearly_summary_custom_buckets(self):
        buckets = runs_util.count_buckets([1, 6, 15, 20, 30])
        years = runs_util.yearly_summary(self._runs(), TZ, buckets)
        # 2 km and 5 km are 5Ks now, 10 km a 10K; 21.1 km is a 42K.
        self.assertEqual(years["2026"]["counts"], {"lt5k": 0, "5k": 2, "10k": 1, "21k": 0, "42k": 0})
        self.assertEqual(years["2025"]["counts"]["42k"], 1)

    def test_distance_bucket(self):
        bucket = runs_util.distance_bucket
        self.assertIsNone(bucket(None))
        self.assertIsNone(bucket(0))
        self.assertEqual(bucket(0.8), "lt5k")
        self.assertEqual(bucket(4.499), "lt5k")
        self.assertEqual(bucket(4.5), "5k")
        self.assertEqual(bucket(9.499), "5k")
        self.assertEqual(bucket(9.5), "10k")
        self.assertEqual(bucket(20.499), "10k")
        self.assertEqual(bucket(20.5), "21k")
        self.assertEqual(bucket(41.499), "21k")
        self.assertEqual(bucket(41.5), "42k")
        self.assertEqual(bucket(47.999), "42k")
        self.assertIsNone(bucket(48.0))  # ultras are not counted

    def test_run_details(self):
        # Shape of GET /my/activities/{id} (seen on the public example account).
        lats = [-1] + [40.68 + i / 10000 for i in range(999)]
        lons = [-1] + [-73.92 - i / 10000 for i in range(999)]
        activity = {
            "city": "New York", "state": "New York", "country": "USA", "countryCode": "us",
            "startLatitude": 40.68, "startLongitude": -73.92,
            "heartRateAverage": 151, "elevationGain": 12.5, "temperature": -1,
            "recordingKeys": ["distance", "latitude", "longitude", "clock"],
            "recordingValues": [list(range(1000)), lats, lons, list(range(1000))],
        }
        details = runs_util.run_details(activity, max_points=100)
        self.assertEqual((details["city"], details["state"], details["country"]), ("New York", "New York", "USA"))
        self.assertEqual(details["country_code"], "us")
        self.assertEqual(details["heart_rate_average"], 151.0)
        self.assertIsNone(details["temperature"])  # -1 means unknown
        track = details["track"]
        self.assertEqual(len(track), 100)
        self.assertEqual(track[0], [40.68, -73.92])  # the -1 gap is dropped
        self.assertEqual(track[-1], [round(40.68 + 998 / 10000, 6), round(-73.92 - 998 / 10000, 6)])

    def test_run_details_without_gps(self):
        details = runs_util.run_details({"city": "", "recordingKeys": ["distance"], "recordingValues": [[1, 2]]})
        self.assertIsNone(details["city"])
        self.assertEqual(details["track"], [])

    def test_get_activity(self):
        client, session = _client(lambda path, params: (200, {"activityId": 7, "city": "Rio"}))
        self.assertEqual(run(client.async_get_activity("7"))["city"], "Rio")
        self.assertEqual(session.calls[-1][0], "my/activities/7")

    def test_best_days(self):
        best = runs_util.best_days(self._runs(), TZ)
        # 2026-10-12 has two runs (5 + 2 km): 7 km, less than the 10 km day.
        self.assertEqual(best["2026"], {"date": "2026-10-19", "km": 10.0})
        self.assertEqual(best["2025"], {"date": "2025-12-31", "km": 21.1})
        runs = self._runs() + [
            api.SmashrunRun.from_json(
                {"runId": 6, "startTime": "2026-10-12T18:00:00-03:00", "distance": 4, "duration": 1200}, TZ
            )
        ]
        # Now 2026-10-12 adds up to 11 km.
        self.assertEqual(runs_util.best_days(runs, TZ)["2026"], {"date": "2026-10-12", "km": 11.0})

    def test_weekly_summary(self):
        weeks = runs_util.weekly_summary(self._runs(), TZ)
        # 2026-10-12 is a Monday of ISO week 42; 2026-10-19 of week 43.
        self.assertEqual(weeks["2026"][41], 7.0)
        self.assertEqual(weeks["2026"][42], 10.0)
        self.assertEqual(len(weeks["2026"]), 53)
        # 2025-12-31 (Wednesday) is in ISO week 1 of 2026.
        self.assertEqual(weeks["2026"][0], 21.1)
        self.assertNotIn("2025", weeks)

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
        runs.append(
            api.SmashrunRun.from_json(  # 9.6 km: in the 10K range but short of 10 km
                {"runId": 6, "startTime": "2026-04-01T07:00:00-03:00", "distance": 9.6, "duration": 2000}, TZ
            )
        )
        records = runs_util.personal_records(runs, TZ)
        five = records["all"]["5k"]
        self.assertEqual(five["run_id"], "5")
        self.assertEqual(five["pace_s_per_km"], 250.0)
        self.assertEqual(five["time_s"], 1240.0)  # the run's real time, not scaled to 5 km
        # 10K: only runs of the 10K range count. The half marathon (run 4) is
        # faster per km but is a 21K; the 9.6 km run is too short.
        self.assertEqual(records["all"]["10k"]["run_id"], "3")
        self.assertEqual(records["all"]["10k"]["time_s"], 3000.0)
        # The run's own totals come along, for the card's detail.
        self.assertEqual(records["all"]["21k"]["run_distance_km"], 21.1)
        self.assertEqual(records["all"]["21k"]["run_duration_s"], 6300.0)
        # 21K: run 4; 42K: nobody.
        self.assertEqual(records["all"]["21k"]["run_id"], "4")
        self.assertEqual(records["all"]["21k"]["time_s"], 6300.0)  # 21.1 km as raced
        self.assertIsNone(records["all"]["42k"])
        # Per year: 2025 only has the half marathon, so no 5K or 10K.
        self.assertIsNone(records["2025"]["5k"])
        self.assertIsNone(records["2025"]["10k"])
        self.assertEqual(records["2025"]["21k"]["run_id"], "4")
        self.assertEqual(records["2026"]["5k"]["run_id"], "5")
        self.assertIsNone(records["2026"]["21k"])

    def test_personal_records_custom_ranges(self):
        # With a 10K range starting at 15 km, the 10 km run becomes a 5K.
        runs = [self._runs()[2]]
        buckets = runs_util.count_buckets([4.5, 15, 20.5, 41.5, 48])
        records = runs_util.personal_records(runs, TZ, buckets)
        self.assertIsNone(records["all"]["10k"])
        self.assertEqual(records["2026"]["5k"]["run_id"], "3")
        self.assertEqual(records["2026"]["5k"]["time_s"], 3000.0)

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
