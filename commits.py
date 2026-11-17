"""Fetch authored commits without truncating GitHub's search results."""

from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import time


# GitHub allows 30 searches a minute. Spacing them out also makes its secondary rate limit less likely.
SEARCH_INTERVAL = 2
PER_PAGE = 100
_next_search = 0.0


def search_commits(username, start, end):
    global _next_search
    stamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"author:{username} author-date:{stamp(start)}..{stamp(end)}"
    for attempt in range(3):
        wait = _next_search - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        result = subprocess.run(
            ["gh", "api", "search/commits", "--include", "-X", "GET", "-f", f"q={query}",
             "-f", f"per_page={PER_PAGE}", "-f", "sort=author-date", "-f", "order=asc"],
            capture_output=True, text=True,
        )
        _next_search = time.monotonic() + SEARCH_INTERVAL
        header, _, body = result.stdout.replace("\r\n", "\n").partition("\n\n")
        if result.returncode == 0:
            return json.loads(body)
        lines = header.splitlines()
        status = lines[0].split()[1] if lines else ""
        headers = {name.lower(): value.strip() for line in lines[1:] if ":" in line
                   for name, value in [line.split(":", 1)]}
        exhausted = headers.get("x-ratelimit-remaining") == "0"
        secondary = "secondary rate limit" in result.stderr.lower()
        limited = status in ["403", "429"] and (exhausted or secondary or "retry-after" in headers)
        if not limited or attempt == 2:
            raise RuntimeError(result.stderr.strip() or "GitHub commit search failed")
        if "retry-after" in headers:
            delay = float(headers["retry-after"])
        elif exhausted:
            delay = float(headers["x-ratelimit-reset"]) - time.time() + 1
        else:
            # GitHub's guidance for a secondary limit without headers: wait a minute, then longer.
            delay = 60 * 2 ** attempt
        delay = max(1, min(120, delay))
        print(f"GitHub search rate limit reached; retrying in {delay:.0f}s...", file=sys.stderr)
        time.sleep(delay)


def fetch_ytd_commits(username, as_of):
    end = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0)
    start = datetime(end.year, 1, 1, tzinfo=timezone.utc)
    authored = lambda item: datetime.fromisoformat(
        item["commit"]["author"]["date"].replace("Z", "+00:00")).astimezone(timezone.utc)

    def search_from(first):
        # A search that times out on GitHub's side returns incomplete results; asking again usually works.
        for attempt in range(3):
            response = search_commits(username, first, end)
            if not response["incomplete_results"] and len(response["items"]) == min(PER_PAGE, response["total_count"]):
                return response["items"]
            print(f"GitHub search returned incomplete results from {first:%Y-%m-%dT%H:%M:%SZ}; retrying...", file=sys.stderr)
        raise RuntimeError("GitHub commit search kept returning incomplete results; run --refresh again")

    # Each search starts at the latest author date seen so far, instead of asking for page 2, 3, ...
    # GitHub can report a different total_count on each request, which shifts numbered pages,
    # and numbered pages stop at 1,000 results. Commits in the boundary second come back twice.
    commits = {}
    cursor = start
    while True:
        items = search_from(cursor)
        for item in items:
            commits[item["sha"]] = {"sha": item["sha"], "authoredAt": item["commit"]["author"]["date"]}
        if len(items) < PER_PAGE:
            return {"asOf": as_of, "commits": list(commits.values())}
        latest = max(authored(item) for item in items)
        if latest <= cursor:
            raise RuntimeError(f"GitHub cannot page past {PER_PAGE} commits authored in the same second")
        cursor = latest


def daily_commit_counts(snapshot):
    end = datetime.fromisoformat(snapshot["asOf"].replace("Z", "+00:00")).astimezone(timezone.utc)
    start = datetime(end.year, 1, 1, tzinfo=timezone.utc)
    days = {}
    day = start.date()
    while day <= end.date():
        days[day.isoformat()] = 0
        day += timedelta(days=1)
    seen = set()
    for commit in snapshot["commits"]:
        if commit["sha"] in seen:
            continue
        seen.add(commit["sha"])
        instant = datetime.fromisoformat(commit["authoredAt"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if start <= instant <= end:
            days[instant.date().isoformat()] += 1
    return [{"date": day, "count": count} for day, count in days.items()]
