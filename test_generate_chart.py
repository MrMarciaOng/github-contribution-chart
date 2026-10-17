import unittest
from unittest.mock import patch

from generate_chart import aggregate, connection_nodes, fetch_activity


class ActivityTests(unittest.TestCase):
    def test_monthly_totals_use_event_dates_and_count_each_activity_once(self):
        user = {"y2026": {
            "endedAt": "2026-01-31T23:59:59Z",
            "contributionCalendar": {"totalContributions": 9, "weeks": [{"contributionDays": [
                {"date": "2025-12-31", "contributionCount": 4},
                {"date": "2026-01-01", "contributionCount": 2},
                {"date": "2026-01-31", "contributionCount": 3},
            ]}]},
        }}
        events = [
            {"id": "comment", "kind": "comments", "occurredAt": "2026-02-01T00:30:00+01:00"},
            {"id": "comment", "kind": "comments", "occurredAt": "2026-02-01T00:30:00+01:00"},
            {"id": "pr", "kind": "prs_closed", "occurredAt": "2026-01-31T23:00:00Z"},
            {"id": "future", "kind": "comments", "occurredAt": "2026-02-01T00:00:00Z"},
            {"id": "old", "kind": "comments", "occurredAt": "2025-12-31T10:00:00Z"},
        ]

        monthly, as_of = aggregate(user, 2026, events)

        self.assertEqual(monthly, [{
            "month": "2026-01", "count": 7,
            "profile_contributions": 5, "comments": 1, "prs_closed": 1,
        }])
        self.assertEqual(as_of, "2026-01-31T23:59:59Z")

    def test_comments_include_conversation_and_commit_comments_and_merge_is_one_closure(self):
        def connections(username, field, fields, arguments=""):
            return iter({
                "issueComments": [{"id": "conversation", "createdAt": "2026-01-01T00:00:00Z"}],
                "commitComments": [{"id": "commit", "createdAt": "2026-01-02T00:00:00Z"}],
                "pullRequests": [{"id": "merged", "closedAt": "2026-01-03T00:00:00Z", "mergedAt": "2026-01-03T00:00:00Z"}],
            }[field])

        with patch("generate_chart.connection_nodes", side_effect=connections):
            events = fetch_activity("example")

        self.assertEqual([event["kind"] for event in events], ["comments", "comments", "prs_closed"])
        self.assertEqual(events[-1]["occurredAt"], "2026-01-03T00:00:00Z")

    def test_historical_connections_continue_until_the_last_page(self):
        pages = [
            {"data": {"user": {"issueComments": {"nodes": [{"id": "first"}], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}}},
            {"data": {"user": {"issueComments": {"nodes": [{"id": "last"}], "pageInfo": {"hasNextPage": False, "endCursor": "end"}}}}},
        ]
        with patch("github_api.graphql", side_effect=pages) as api:
            nodes = list(connection_nodes("example", "issueComments", "id"))

        self.assertEqual(nodes, [{"id": "first"}, {"id": "last"}])
        self.assertEqual(api.call_count, 2)

    def test_invalid_pagination_fails_instead_of_returning_partial_history(self):
        page = {"data": {"user": {"issueComments": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}}}}}
        with patch("github_api.graphql", return_value=page):
            with self.assertRaisesRegex(RuntimeError, "did not advance pagination"):
                list(connection_nodes("example", "issueComments", "id"))


if __name__ == "__main__":
    unittest.main()
