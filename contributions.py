"""Daily contributions from shipped PRs, submitted reviews, and comments."""

from datetime import datetime, timedelta, timezone
import json

from github_api import graphql, connection_nodes


REVIEW_FIELDS = "id state submittedAt"
PAGE_FIELDS = "pageInfo { hasNextPage endCursor }"


def reviewed_prs(username, as_of):
    review_query = f'reviews(first:100,author:{json.dumps(username)}) {{ nodes {{ {REVIEW_FIELDS} }} {PAGE_FIELDS} }}'
    end = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)

    def search_range(first, last):
        stamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
        search = f"is:pr reviewed-by:{username} created:{stamp(first)}..{stamp(last)}"
        after = None
        seen = set()
        expected = None
        while True:
            cursor = f",after:{json.dumps(after)}" if after is not None else ""
            query = f'query {{ search(type:ISSUE,query:{json.dumps(search)},first:100{cursor}) {{ issueCount nodes {{ ... on PullRequest {{ id {review_query} }} }} {PAGE_FIELDS} }} }}'
            connection = graphql(query)["data"]["search"]
            if connection["issueCount"] > 1000:
                seconds = int((last - first).total_seconds())
                if after is not None or seconds == 0:
                    raise RuntimeError("Cannot retrieve complete reviewed-PR history; run --refresh again")
                middle = first + timedelta(seconds=seconds // 2)
                count = 0
                for lower, upper in [(first, middle), (middle + timedelta(seconds=1), last)]:
                    for node in search_range(lower, upper):
                        count += 1
                        yield node
                if count != connection["issueCount"]:
                    raise RuntimeError("Reviewed-PR search changed while splitting; run --refresh again")
                return
            if expected is None:
                expected = connection["issueCount"]
            if expected != connection["issueCount"]:
                raise RuntimeError("Reviewed-PR search changed during pagination; run --refresh again")
            for node in connection["nodes"]:
                if node["id"] not in seen:
                    seen.add(node["id"])
                    yield node
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                if len(seen) != expected:
                    raise RuntimeError("GitHub returned incomplete reviewed-PR history")
                return
            if not page["endCursor"] or page["endCursor"] == after:
                raise RuntimeError("GitHub did not advance reviewed-PR pagination")
            after = page["endCursor"]

    yield from search_range(datetime(1970, 1, 1, tzinfo=timezone.utc), end)


def node_connection(node_id, type_name, field, fields, initial=None, arguments=""):
    connection = initial
    after = None
    while True:
        if connection is None:
            cursor = f",after:{json.dumps(after)}" if after is not None else ""
            query = f'query {{ node(id:{json.dumps(node_id)}) {{ ... on {type_name} {{ {field}(first:100{arguments}{cursor}) {{ nodes {{ {fields} }} {PAGE_FIELDS} }} }} }} }}'
            connection = graphql(query)["data"]["node"][field]
        yield from connection["nodes"]
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            return
        if not page["endCursor"] or page["endCursor"] == after:
            raise RuntimeError(f"GitHub did not advance {field} pagination")
        after = page["endCursor"]
        connection = None


def fetch_contributions(username, activity, as_of):
    events = []
    for event in activity:
        if event["kind"] == "comments":
            events.append(event)
        elif event.get("mergedAt"):
            events.append({"id": event["id"], "kind": "prs_shipped", "occurredAt": event["mergedAt"]})
    for comment in connection_nodes(username, "repositoryDiscussionComments", "id createdAt publishedAt"):
        if comment["publishedAt"]:
            events.append({"id": comment["id"], "kind": "comments", "occurredAt": comment["publishedAt"]})

    reviews = {}
    for pr in reviewed_prs(username, as_of):
        for review in node_connection(pr["id"], "PullRequest", "reviews", REVIEW_FIELDS,
                                      initial=pr["reviews"], arguments=f",author:{json.dumps(username)}"):
            if review["state"] != "PENDING" and review["submittedAt"]:
                reviews[review["id"]] = review
    for review in reviews.values():
        events.append({"id": review["id"], "kind": "reviews", "occurredAt": review["submittedAt"]})

    review_ids = list(reviews)
    fields = "id createdAt publishedAt state author { login }"
    for start in range(0, len(review_ids), 20):
        ids = review_ids[start:start + 20]
        query = f'query {{ nodes(ids:{json.dumps(ids)}) {{ ... on PullRequestReview {{ id comments(first:100) {{ nodes {{ {fields} }} {PAGE_FIELDS} }} }} }} }}'
        nodes = graphql(query)["data"]["nodes"]
        for review in nodes:
            for comment in node_connection(review["id"], "PullRequestReview", "comments", fields, initial=review["comments"]):
                if comment["state"] != "PENDING" and comment["author"] and comment["author"]["login"].lower() == username.lower():
                    events.append({"id": comment["id"], "kind": "comments", "occurredAt": comment["publishedAt"] or comment["createdAt"]})
    return {"asOf": as_of, "events": events}


def daily_contribution_counts(snapshot):
    end = datetime.fromisoformat(snapshot["asOf"].replace("Z", "+00:00")).astimezone(timezone.utc)
    start = datetime(end.year, 1, 1, tzinfo=timezone.utc)
    days = {}
    day = start.date()
    while day <= end.date():
        days[day.isoformat()] = {"prs_shipped": 0, "reviews": 0, "comments": 0}
        day += timedelta(days=1)
    seen = set()
    for event in snapshot["events"]:
        key = (event["kind"], event["id"])
        if key in seen:
            continue
        seen.add(key)
        instant = datetime.fromisoformat(event["occurredAt"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if start <= instant <= end:
            days[instant.date().isoformat()][event["kind"]] += 1
    return [{"date": day, "count": sum(counts.values()), **counts} for day, counts in days.items()]
