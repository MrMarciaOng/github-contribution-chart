"""Fetch authored commits without truncating GitHub's search results."""

from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import time


# GitHub allows 30 searches a minute. Spacing them out also makes its secondary rate limit less likely.
SEARCH_INTERVAL = 2
_next_search = 0.0


def search_commits(username, start, end, page=1):
    global _next_search
    stamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"author:{username} author-date:{stamp(start)}..{stamp(end)}"
    for attempt in range(3):
        wait = _next_search - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        result = subprocess.run(
            ["gh", "api", "search/commits", "--include", "-X", "GET", "-f", f"q={query}",
             "-f", "per_page=100", "-f", f"page={page}", "-f", "sort=author-date", "-f", "order=asc"],
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

    def fetch_page(first, last, page, total):
        # A search that times out on GitHub's side returns incomplete_results; asking again usually works.
        for attempt in range(3):
            response = search_commits(username, first, last, page)
            if not response["incomplete_results"]:
                if response["total_count"] != total:
                    raise RuntimeError("GitHub commit search changed during pagination; run --refresh again")
                return response["items"]
            print(f"GitHub search returned incomplete results for page {page}; retrying...", file=sys.stderr)
        raise RuntimeError("GitHub commit search kept returning incomplete results; run --refresh again")

    def fetch_range(first, last):
        response = search_commits(username, first, last)
        if response["incomplete_results"] or response["total_count"] > 1000:
            seconds = int((last - first).total_seconds())
            if seconds == 0:
                raise RuntimeError("GitHub cannot return a complete commit search for this second")
            middle = first + timedelta(seconds=seconds // 2)
            return fetch_range(first, middle) + fetch_range(middle + timedelta(seconds=1), last)
        items = response["items"]
        for page in range(2, (response["total_count"] + 99) // 100 + 1):
            items.extend(fetch_page(first, last, page, response["total_count"]))
        if len(items) != response["total_count"]:
            raise RuntimeError("GitHub returned an incomplete commit history")
        return items

    commits = {}
    for item in fetch_range(start, end):
        authored_at = item["commit"]["author"]["date"]
        instant = datetime.fromisoformat(authored_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        if start <= instant <= end:
            commits[item["sha"]] = {"sha": item["sha"], "authoredAt": authored_at}
    return {"asOf": as_of, "commits": list(commits.values())}


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
