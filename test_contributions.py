from collections import Counter
import unittest
from unittest.mock import patch

from contributions import daily_contribution_counts, fetch_contributions, node_connection, reviewed_prs


def page(nodes, more=False, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": cursor}}


class ContributionTests(unittest.TestCase):
    def test_shipped_prs_reviews_and_comments_have_separate_daily_counts(self):
        snapshot = {"asOf": "2026-01-03T12:00:00Z", "events": [
            {"id": "reply", "kind": "comments", "occurredAt": "2026-01-02T00:30:00+01:00"},
            {"id": "reply", "kind": "comments", "occurredAt": "2026-01-02T00:30:00+01:00"},
            {"id": "review-1", "kind": "reviews", "occurredAt": "2026-01-01T10:00:00Z"},
            {"id": "review-2", "kind": "reviews", "occurredAt": "2026-01-01T11:00:00Z"},
            {"id": "merged", "kind": "prs_shipped", "occurredAt": "2026-01-03T09:00:00Z"},
            {"id": "old", "kind": "comments", "occurredAt": "2025-12-31T10:00:00Z"},
            {"id": "future", "kind": "comments", "occurredAt": "2026-01-03T13:00:00Z"},
        ]}
        rows = daily_contribution_counts(snapshot)
        self.assertEqual(rows, [
            {"date": "2026-01-01", "count": 3, "prs_shipped": 0, "reviews": 2, "comments": 1},
            {"date": "2026-01-02", "count": 0, "prs_shipped": 0, "reviews": 0, "comments": 0},
            {"date": "2026-01-03", "count": 1, "prs_shipped": 1, "reviews": 0, "comments": 0},
        ])

    def test_only_merged_prs_submitted_reviews_and_the_users_posted_comments_count(self):
        activity = [
            {"id": "talk", "kind": "comments", "occurredAt": "2026-01-02T00:00:00Z"},
            {"id": "merged", "kind": "prs_closed", "occurredAt": "2026-01-03T00:00:00Z", "mergedAt": "2026-01-03T00:00:00Z"},
            {"id": "cancelled", "kind": "prs_closed", "occurredAt": "2026-01-03T00:00:00Z", "mergedAt": None},
        ]
        reviews = [
            {"id": "review", "state": "APPROVED", "submittedAt": "2026-01-04T12:00:00Z"},
            {"id": "draft", "state": "PENDING", "submittedAt": None},
        ]
        comments = [
            {"id": "inline", "createdAt": "2025-12-31T00:00:00Z", "publishedAt": "2026-01-04T12:00:00Z", "state": "SUBMITTED", "author": {"login": "EXAMPLE"}},
            {"id": "other", "createdAt": "2026-01-04T00:00:00Z", "publishedAt": "2026-01-04T00:00:00Z", "state": "SUBMITTED", "author": {"login": "other-user"}},
            {"id": "draft-reply", "createdAt": "2026-01-04T00:00:00Z", "publishedAt": None, "state": "PENDING", "author": {"login": "EXAMPLE"}},
        ]
        discussion = [
            {"id": "discussion", "createdAt": "2026-01-01T00:00:00Z", "publishedAt": "2026-01-01T00:00:00Z"},
            {"id": "unpublished", "createdAt": "2026-01-01T00:00:00Z", "publishedAt": None},
        ]
        with patch("contributions.connection_nodes", return_value=iter(discussion)), \
                patch("contributions.reviewed_prs", return_value=iter([{"id": "pr", "reviews": page(reviews)}])), \
                patch("contributions.graphql", return_value={"data": {"nodes": [{"id": "review", "comments": page(comments)}]}}):
            snapshot = fetch_contributions("example", activity, "2026-01-05T00:00:00Z")
        self.assertEqual(Counter(event["kind"] for event in snapshot["events"]),
                         {"comments": 3, "prs_shipped": 1, "reviews": 1})
        rows = daily_contribution_counts(snapshot)
        self.assertEqual(rows[3]["comments"], 1)
        self.assertEqual(rows[3]["reviews"], 1)

    def test_inline_comments_continue_beyond_the_first_page(self):
        initial = page([{"id": "first"}], True, "next")
        response = {"data": {"node": {"comments": page([{"id": "last"}])}}}
        with patch("contributions.graphql", return_value=response):
            comments = list(node_connection("review", "PullRequestReview", "comments", "id", initial=initial))
        self.assertEqual(comments, [{"id": "first"}, {"id": "last"}])

    def test_review_discovery_continues_beyond_the_first_search_page(self):
        first = {"data": {"search": {"issueCount": 2, **page([{"id": "first"}], True, "next")}}}
        last = {"data": {"search": {"issueCount": 2, **page([{"id": "last"}])}}}
        with patch("contributions.graphql", side_effect=[first, last]):
            prs = list(reviewed_prs("example", "2026-01-03T12:00:00Z"))
        self.assertEqual(prs, [{"id": "first"}, {"id": "last"}])

    def test_review_discovery_splits_searches_larger_than_1000_prs(self):
        responses = [{"data": {"search": {"issueCount": 1001, **page([])}}}]
        for prefix, total in [("left", 500), ("right", 501)]:
            for start in range(0, total, 100):
                nodes = [{"id": f"{prefix}-{i}"} for i in range(start, min(start+100, total))]
                responses.append({"data": {"search": {"issueCount": total,
                                 **page(nodes, start+100<total, f"{prefix}-{start}")}}})
        with patch("contributions.graphql", side_effect=responses):
            prs = list(reviewed_prs("example", "2026-01-03T12:00:00Z"))
        self.assertEqual(len({pr["id"] for pr in prs}), 1001)


if __name__ == "__main__":
    unittest.main()
