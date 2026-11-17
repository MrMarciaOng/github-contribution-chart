from contextlib import contextmanager
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
import subprocess

from commits import SEARCH_INTERVAL, daily_commit_counts, fetch_ytd_commits, search_commits


def commit(sha, date):
    return {"sha": sha, "commit": {"author": {"date": date}}}


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

    def test_search_paginates_and_deduplicates_the_same_commit_across_repositories(self):
        first = [commit(str(i), "2026-01-01T10:00:00Z") for i in range(100)]
        responses = [
            {"total_count": 101, "incomplete_results": False, "items": first},
            {"total_count": 101, "incomplete_results": False, "items": [first[0]]},
        ]
        with patch("commits.search_commits", side_effect=responses):
            snapshot = fetch_ytd_commits("example", "2026-01-02T12:00:00Z")
        self.assertEqual(len(snapshot["commits"]), 100)

    def test_search_splits_the_date_range_to_retrieve_more_than_1000_commits(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)

        def search(username, first, last, page=1):
            if first == start and last == end:
                return {"total_count": 1001, "incomplete_results": False, "items": []}
            prefix, total = ("left", 500) if first == start else ("right", 501)
            stamp = first.strftime("%Y-%m-%dT%H:%M:%SZ")
            items = [commit(f"{prefix}-{i}", stamp) for i in range((page-1)*100, min(page*100, total))]
            return {"total_count": total, "incomplete_results": False, "items": items}

        with patch("commits.search_commits", side_effect=search):
            snapshot = fetch_ytd_commits("example", end.isoformat())
        self.assertEqual(len(snapshot["commits"]), 1001)
        self.assertEqual(sum(row["count"] for row in daily_commit_counts(snapshot)), 1001)

    def test_incomplete_search_cannot_silently_publish_partial_counts(self):
        response = {"total_count": 1, "incomplete_results": True, "items": []}
        with patch("commits.search_commits", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "cannot return a complete commit search"):
                fetch_ytd_commits("example", "2026-01-01T00:00:00Z")

    def test_search_retries_a_page_that_returns_incomplete_results(self):
        first = [commit(str(i), "2026-01-01T10:00:00Z") for i in range(100)]
        responses = [
            {"total_count": 101, "incomplete_results": False, "items": first},
            {"total_count": 101, "incomplete_results": True, "items": [commit("partial", "2026-01-01T11:00:00Z")]},
            {"total_count": 101, "incomplete_results": False, "items": [commit("100", "2026-01-01T11:00:00Z")]},
        ]
        with patch("commits.search_commits", side_effect=responses) as search:
            snapshot = fetch_ytd_commits("example", "2026-01-02T12:00:00Z")
        shas = {item["sha"] for item in snapshot["commits"]}
        self.assertEqual(len(shas), 101)
        self.assertNotIn("partial", shas)
        self.assertEqual(search.call_count, 3)

    def test_search_fails_when_a_page_stays_incomplete(self):
        first = {"total_count": 101, "incomplete_results": False,
                 "items": [commit(str(i), "2026-01-01T10:00:00Z") for i in range(100)]}
        incomplete = {"total_count": 101, "incomplete_results": True, "items": []}
        with patch("commits.search_commits", side_effect=[first, incomplete, incomplete, incomplete]):
            with self.assertRaisesRegex(RuntimeError, "kept returning incomplete results"):
                fetch_ytd_commits("example", "2026-01-02T12:00:00Z")

    def test_search_fails_when_the_total_changes_during_pagination(self):
        first = {"total_count": 101, "incomplete_results": False,
                 "items": [commit(str(i), "2026-01-01T10:00:00Z") for i in range(100)]}
        grown = {"total_count": 102, "incomplete_results": False, "items": [commit("100", "2026-01-01T11:00:00Z")]}
        with patch("commits.search_commits", side_effect=[first, grown]):
            with self.assertRaisesRegex(RuntimeError, "changed during pagination"):
                fetch_ytd_commits("example", "2026-01-02T12:00:00Z")

    def test_search_waits_between_requests(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with fake_search([OK, OK]) as sleeps:
            search_commits("example", now, now)
            search_commits("example", now, now, page=2)
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
