from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import subprocess

from commits import SEARCH_INTERVAL, daily_commit_counts, fetch_ytd_commits, search_commits


def commit(sha, date):
    return {"sha": sha, "commit": {"author": {"date": date}}}


def at(**offset):
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(**offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


def github(history, total_drift={}, replies={}):
    """Answer search_commits like GitHub: the earliest 100 matches by author date, plus their count.

    total_drift and replies change the response to the nth request (counting from 1)."""
    authored = lambda item: datetime.fromisoformat(item["commit"]["author"]["date"].replace("Z", "+00:00"))
    requests = []

    def search(username, first, last):
        requests.append(first)
        if len(requests) in replies:
            return replies[len(requests)]
        matches = sorted((item for item in history if first <= authored(item) <= last), key=authored)
        return {"total_count": len(matches) + total_drift.get(len(requests), 0),
                "incomplete_results": False, "items": matches[:100]}
    return search


OK = subprocess.CompletedProcess([], 0, 'HTTP/2.0 200 OK\r\n\r\n{"total_count": 0, "items": [], "incomplete_results": false}', '')
SECONDARY_LIMIT = subprocess.CompletedProcess(
    [], 1, 'HTTP/2.0 403 Forbidden\r\nx-ratelimit-remaining: 15\r\n\r\n{}',
    'gh: You have exceeded a secondary rate limit. Please wait a few minutes before you try again. (HTTP 403)')


@contextmanager
def fake_search(responses):
    """Run search_commits against canned gh output; sleeping advances a fake clock."""
    clock = {"now": 1000.0}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    with patch("commits._next_search", 0.0), patch("commits.subprocess.run", side_effect=responses), \
            patch("commits.time.monotonic", lambda: clock["now"]), patch("commits.time.sleep", sleep), \
            patch("commits.time.time", return_value=10):
        yield sleeps


class DailyCommitTests(unittest.TestCase):
    def test_daily_chart_includes_zero_days_leap_day_and_only_ytd_utc_commits(self):
        snapshot = {"asOf": "2024-03-01T12:00:00Z", "commits": [
            {"sha": "prior-year", "authoredAt": "2024-01-01T01:00:00+02:00"},
            {"sha": "new-year", "authoredAt": "2023-12-31T23:00:00-02:00"},
            {"sha": "leap", "authoredAt": "2024-02-29T12:00:00Z"},
            {"sha": "leap", "authoredAt": "2024-02-29T12:00:00Z"},
            {"sha": "future", "authoredAt": "2024-03-01T13:00:00Z"},
        ]}

        rows = daily_commit_counts(snapshot)

        self.assertEqual(len(rows), 61)
        self.assertEqual(rows[0], {"date": "2024-01-01", "count": 1})
        self.assertEqual(rows[1], {"date": "2024-01-02", "count": 0})
        self.assertEqual(rows[-2], {"date": "2024-02-29", "count": 1})
        self.assertEqual(rows[-1], {"date": "2024-03-01", "count": 0})
        self.assertEqual(sum(row["count"] for row in rows), 2)

    def test_zero_commits_still_produces_a_daily_chart(self):
        self.assertEqual(daily_commit_counts({"asOf": "2026-01-01T12:00:00Z", "commits": []}),
                         [{"date": "2026-01-01", "count": 0}])

    def test_search_pages_by_author_date_past_1000_commits(self):
        history = [commit(str(i), at(minutes=i)) for i in range(1001)]
        with patch("commits.search_commits", side_effect=github(history)) as search:
            snapshot = fetch_ytd_commits("example", "2026-01-02T12:00:00Z")
        self.assertEqual(len(snapshot["commits"]), 1001)
        self.assertEqual(sum(row["count"] for row in daily_commit_counts(snapshot)), 1001)
        self.assertEqual(search.call_count, 11)

    def test_search_deduplicates_the_same_commit_across_repositories(self):
        history = [commit(str(i), at(minutes=i)) for i in range(150)] + [commit("0", at(minutes=0))]
        with patch("commits.search_commits", side_effect=github(history)):
            snapshot = fetch_ytd_commits("example", "2026-01-02T12:00:00Z")
        self.assertEqual(len(snapshot["commits"]), 150)

    def test_search_keeps_commits_that_share_the_page_boundary_second(self):
        history = ([commit(f"early-{i}", at(minutes=i)) for i in range(90)]
                   + [commit(f"same-{i}", at(hours=5)) for i in range(30)])
        with patch("commits.search_commits", side_effect=github(history)):
            snapshot = fetch_ytd_commits("example", "2026-01-02T12:00:00Z")
        self.assertEqual(len(snapshot["commits"]), 120)

    def test_search_fails_when_more_than_a_page_of_commits_share_one_second(self):
        history = [commit(str(i), at(hours=5)) for i in range(101)]
        with patch("commits.search_commits", side_effect=github(history)):
            with self.assertRaisesRegex(RuntimeError, "same second"):
                fetch_ytd_commits("example", "2026-01-02T12:00:00Z")

    def test_search_ignores_a_total_that_changes_between_requests(self):
        history = [commit(str(i), at(minutes=i)) for i in range(250)]
        with patch("commits.search_commits", side_effect=github(history, total_drift={2: 1})):
            snapshot = fetch_ytd_commits("example", "2026-01-02T12:00:00Z")
        self.assertEqual(len(snapshot["commits"]), 250)

    def test_search_retries_a_page_that_returns_incomplete_results(self):
        history = [commit(str(i), at(minutes=i)) for i in range(150)]
        stale = {"total_count": 150, "incomplete_results": True, "items": [commit("stale", at(minutes=1))]}
        with patch("commits.search_commits", side_effect=github(history, replies={2: stale})) as search:
            snapshot = fetch_ytd_commits("example", "2026-01-02T12:00:00Z")
        shas = {item["sha"] for item in snapshot["commits"]}
        self.assertEqual(len(shas), 150)
        self.assertNotIn("stale", shas)
        self.assertEqual(search.call_count, 3)

    def test_search_fails_when_results_stay_incomplete(self):
        incomplete = {"total_count": 1, "incomplete_results": True, "items": []}
        with patch("commits.search_commits", return_value=incomplete):
            with self.assertRaisesRegex(RuntimeError, "kept returning incomplete results"):
                fetch_ytd_commits("example", "2026-01-02T12:00:00Z")

    def test_search_cannot_silently_publish_a_page_shorter_than_its_total(self):
        short = {"total_count": 5, "incomplete_results": False, "items": [commit(str(i), at(minutes=i)) for i in range(4)]}
        with patch("commits.search_commits", return_value=short):
            with self.assertRaisesRegex(RuntimeError, "kept returning incomplete results"):
                fetch_ytd_commits("example", "2026-01-02T12:00:00Z")

    def test_search_waits_between_requests(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with fake_search([OK, OK]) as sleeps:
            search_commits("example", now, now)
            search_commits("example", now, now)
        self.assertEqual(sleeps, [SEARCH_INTERVAL])

    def test_search_waits_until_the_primary_rate_limit_resets_within_a_bound(self):
        exhausted = subprocess.CompletedProcess(
            [], 1, 'HTTP/2.0 403 Forbidden\r\nx-ratelimit-remaining: 0\r\nx-ratelimit-reset: 500\r\n\r\n{}', 'rate limit exceeded')
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with fake_search([exhausted, OK]) as sleeps:
            response = search_commits("example", now, now)
        self.assertEqual(response["total_count"], 0)
        self.assertEqual(sleeps, [120])

    def test_search_backs_off_from_a_secondary_rate_limit_without_headers(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with fake_search([SECONDARY_LIMIT, SECONDARY_LIMIT, OK]) as sleeps:
            response = search_commits("example", now, now)
        self.assertEqual(response["total_count"], 0)
        self.assertEqual(sleeps, [60, 120])

    def test_search_gives_up_after_three_rate_limited_attempts(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with fake_search([SECONDARY_LIMIT] * 3):
            with self.assertRaisesRegex(RuntimeError, "secondary rate limit"):
                search_commits("example", now, now)

    def test_search_reports_authentication_errors_without_waiting(self):
        response = subprocess.CompletedProcess([], 1, 'HTTP/2.0 401 Unauthorized\n\n{}', 'gh: Bad credentials')
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with fake_search([response]) as sleeps:
            with self.assertRaisesRegex(RuntimeError, 'Bad credentials'):
                search_commits('example', now, now)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
