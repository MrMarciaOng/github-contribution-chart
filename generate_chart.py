#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["resvg-py==0.5.0"]
# ///
"""Generate daily contribution and commit charts, plus monthly activity, with gh."""

import argparse
import base64
from collections import defaultdict
import csv
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
from urllib.request import urlopen

from commits import daily_commit_counts, fetch_ytd_commits
from github_api import graphql, connection_nodes
from contributions import daily_contribution_counts, fetch_contributions


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
CHARTS = {
    "activity": ("activities", "Activities",
                 "Monthly profile contributions plus issue/PR conversation comments, commit comments, and closures of authored PRs. The latest month is partial."),
    "commits": ("commits YTD", "Commits / day",
                "Daily authored commits on accessible default branches, grouped by UTC author date. Today is partial."),
    "contributions": ("contributions YTD", "Contributions / day",
                      "Daily merged PRs authored by the account, submitted PR reviews, and posted comments, grouped by UTC date. Today is partial."),
    "profile_contributions": ("profile contributions", "Contributions",
                              "Monthly GitHub profile contributions, such as commits, opened PRs and issues, and submitted PR reviews. The latest month is partial."),
    "prs_closed": ("authored PRs closed", "PRs closed",
                   "Monthly authored PRs that were merged or closed without merging, counted on their latest UTC closure dates. The latest month is partial."),
}


def fetch_activity(username):
    events = []
    for field in ["issueComments", "commitComments"]:
        for node in connection_nodes(username, field, "id createdAt"):
            events.append({"id": node["id"], "kind": "comments", "occurredAt": node["createdAt"]})
    for node in connection_nodes(username, "pullRequests", "id closedAt mergedAt", ",states:[CLOSED,MERGED]"):
        occurred_at = node["closedAt"] or node["mergedAt"]
        if occurred_at:
            events.append({"id": node["id"], "kind": "prs_closed", "occurredAt": occurred_at, "mergedAt": node["mergedAt"]})
    return events


def fetch(username):
    user = graphql(
        "query { user(login:" + json.dumps(username)
        + ") { login name url avatarUrl createdAt } }"
    )["data"]["user"]
    if user is None:
        raise ValueError(f"GitHub user {username!r} was not found")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fields = []
    for year in range(int(user["createdAt"][:4]), now.year + 1):
        end = (f"{year}-12-31T23:59:59Z" if year < now.year
               else now.isoformat().replace("+00:00", "Z"))
        fields.append(
            f'y{year}: contributionsCollection(from:"{year}-01-01T00:00:00Z",to:"{end}")'
            " { startedAt endedAt totalCommitContributions restrictedContributionsCount"
            " contributionCalendar { totalContributions weeks"
            " { contributionDays { date contributionCount } } } }"
        )
    response = graphql(
        "query { user(login:" + json.dumps(username)
        + ") { login name url avatarUrl createdAt " + " ".join(fields) + " } }"
    )
    response["schemaVersion"] = 4
    response["activity"] = fetch_activity(username)
    response["commitsYtd"] = fetch_ytd_commits(user["login"], now.isoformat().replace("+00:00", "Z"))
    response["contributionsYtd"] = fetch_contributions(user["login"], response["activity"], now.isoformat().replace("+00:00", "Z"))
    return response


def aggregate(user, from_year, events):
    months = defaultdict(lambda: {"profile_contributions": 0, "comments": 0, "prs_closed": 0})
    dates = set()
    ends = []
    for key, collection in user.items():
        if not (key.startswith("y") and key[1:].isdigit()):
            continue
        days = [day for week in collection["contributionCalendar"]["weeks"]
                for day in week["contributionDays"]]
        assert sum(d["contributionCount"] for d in days) == collection["contributionCalendar"]["totalContributions"]
        ends.append(collection["endedAt"])
        for day in days:
            date = day["date"]
            assert date not in dates, f"Duplicate contribution day: {date}"
            dates.add(date)
            if int(date[:4]) >= from_year:
                months[date[:7]]["profile_contributions"] += day["contributionCount"]
    if not months:
        raise ValueError("No data exists for the selected date range")
    as_of = max(ends)
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    seen = set()
    for event in events:
        key = (event["kind"], event["id"])
        if key in seen:
            continue
        seen.add(key)
        occurred_at = datetime.fromisoformat(event["occurredAt"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if occurred_at.year >= from_year and occurred_at <= cutoff:
            months[occurred_at.strftime("%Y-%m")][event["kind"]] += 1
    return [{"month": month, "count": sum(counts.values()), **counts}
            for month, counts in sorted(months.items())], as_of


def svg_chart(user, rows, avatar_uri, as_of, kind="activity"):
    daily = kind in ("commits", "contributions")
    collaboration = kind == "contributions"
    total = sum(row["count"] for row in rows)
    max_count = max(row["count"] for row in rows)
    rough_step = max(1, max_count / 4)
    magnitude = 10 ** math.floor(math.log10(rough_step))
    ceiling = 4 * next(unit * magnitude for unit in [1, 2, 5, 10] if unit * magnitude >= rough_step)
    left, right, top, bottom = 36, 792, 112, 282
    step = (right - left) / len(rows)
    date_key = "date" if daily else "month"
    date_label = lambda value: datetime.strptime(value, "%Y-%m-%d" if daily else "%Y-%m").strftime("%d %b %Y" if daily else "%b %Y").lstrip("0")
    date_range = f'{date_label(rows[0][date_key])} – {date_label(rows[-1][date_key])}'
    measure, axis_label, description = CHARTS[kind]
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="900" height="390" viewBox="0 0 900 390" role="img">',
        f'<title>{escape(user["login"])}: {total:,} {measure}</title>',
        f'<desc>{description}</desc>',
        '<rect x="1" y="1" width="898" height="388" rx="9" fill="#ffffff" stroke="#d1d9e0" stroke-width="1.5"/>',
        '<defs><clipPath id="avatar"><circle cx="61" cy="56" r="27"/></clipPath></defs>',
        f'<image x="34" y="29" width="54" height="54" xlink:href="{avatar_uri}" clip-path="url(#avatar)"/>',
        '<g font-family="Arial, Helvetica, sans-serif">',
        f'<text x="104" y="51" font-size="21" font-weight="600" fill="#0969da">{escape(user["login"])}</text>',
        f'<text x="104" y="77" font-size="17" fill="#59636e">{total:,} {measure}</text>',
        f'<text x="862" y="53" text-anchor="end" font-size="14" fill="#59636e">{date_range}</text>',
    ]
    for i in range(5):
        count = ceiling * i / 4
        y = bottom - (bottom - top) * count / ceiling
        pieces.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="#e3e8ed" stroke-dasharray="4 4"/>')
        pieces.append(f'<text x="802" y="{y + 5:.2f}" font-size="14" fill="#59636e">{count:,.0f}</text>')
    pieces.append(f'<text x="873" y="197" transform="rotate(90 873 197)" text-anchor="middle" font-size="15" fill="#59636e">{axis_label}</text>')
    if daily:
        tick_indices = [i for i, row in enumerate(rows) if row["date"][5:] in ["01-01", "03-01", "05-01", "07-01", "09-01", "11-01"]]
    else:
        tick_indices = [i for i, row in enumerate(rows)
                        if row["month"].endswith("-01") and (int(row["month"][:4]) - int(rows[0]["month"][:4])) % 2 == 0]
    tick_indices = [i for i in tick_indices if i == 0 or (len(rows) - 1 - i) * step >= 110]
    for index in tick_indices:
        x = left + step * (index + .5)
        pieces.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="#e3e8ed" stroke-dasharray="4 4"/>')
        tick_label = datetime.strptime(rows[index]["date"], "%Y-%m-%d").strftime("%b") if daily else date_label(rows[index][date_key])
        pieces.append(f'<text x="{x:.2f}" y="312" text-anchor="{"start" if index == 0 else "middle"}" font-size="14" fill="#59636e">{tick_label}</text>')
    for i, row in enumerate(rows):
        x = left + step * i + .6
        running = 0
        series = [("prs_shipped", "#388bfd"), ("reviews", "#1a7f37"), ("comments", "#8250df")] if collaboration else [("count", "#388bfd")]
        for key, color in series:
            height = (bottom - top) * row[key] / ceiling
            running += height
            pieces.append(f'<rect x="{x:.2f}" y="{bottom-running:.2f}" width="{max(.2, step-1.2):.2f}" height="{height:.2f}" fill="{color}" opacity="{.65 if i == len(rows)-1 else 1}"/>')
    peak_index = max(range(len(rows)), key=lambda i: rows[i]["count"])
    peak_x = left + step * (peak_index + .5)
    peak_y = bottom - (bottom - top) * max_count / ceiling
    pieces.append(f'<text x="{peak_x:.2f}" y="{peak_y-10:.2f}" text-anchor="middle" font-size="13" fill="#1f2328">{max_count:,}</text>')
    pieces.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#d1d9e0"/>')
    if len(rows) > 1:
        pieces.append(f'<text x="{right}" y="312" text-anchor="end" font-size="14" fill="#59636e">{date_label(rows[-1][date_key])}</text>')
    stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00")).strftime("%d %b %Y").lstrip("0")
    if collaboration:
        for x, key, label, color in [(36, "prs_shipped", "PRs shipped", "#388bfd"), (295, "reviews", "PR reviews", "#1a7f37"), (555, "comments", "Comments", "#8250df")]:
            count = sum(row[key] for row in rows)
            pieces.append(f'<rect x="{x}" y="335" width="9" height="9" fill="{color}"/>')
            pieces.append(f'<text x="{x+16}" y="344" font-size="12" fill="#59636e">{label}: {count:,}</text>')
        summary = None
        note = f"Accessible public and private repositories · UTC dates · today partial · as of {stamp}"
    elif daily:
        summary = "Authored commits · default branches · UTC dates · zero-commit days included"
        note = f"Accessible public and private repositories · today partial · as of {stamp}"
    elif kind == "activity":
        profile = sum(row["profile_contributions"] for row in rows)
        comments = sum(row["comments"] for row in rows)
        closed = sum(row["prs_closed"] for row in rows)
        summary = f"{profile:,} profile contributions + {comments:,} comments + {closed:,} authored PR closures"
        note = f"Available private activity included · latest month partial · as of {stamp}"
    elif kind == "profile_contributions":
        summary = "GitHub profile calendar · commits, opened PRs and issues, PR reviews, and discussions"
        note = f"Available private activity included · latest month partial · as of {stamp}"
    else:
        summary = "Authored PRs merged or closed without merging · counted on latest UTC closure date"
        note = f"Accessible public and private repositories · latest month partial · as of {stamp}"
    if summary:
        pieces.append(f'<text x="36" y="344" font-size="12" fill="#59636e">{summary}</text>')
    pieces.append(f'<text x="36" y="369" font-size="12" fill="#59636e">{note}</text>')
    pieces.append('</g></svg>')
    return "\n".join(pieces)


def export_chart(stem, payload):
    import resvg_py
    rows = payload["daily"] if "daily" in payload else payload["monthly"]
    svg = svg_chart(payload, rows, payload["avatar"], payload["asOf"], payload["kind"])
    (OUTPUT / f"{stem}.svg").write_text(svg + "\n")
    (OUTPUT / f"{stem}.png").write_bytes(resvg_py.svg_to_bytes(svg_string=svg, width=2400))
    with (OUTPUT / f"{stem}.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (OUTPUT / f"{stem}-data.json").write_text(json.dumps(payload, indent=2) + "\n")
    template = (ROOT / "chart-template.html").read_text()
    (OUTPUT / f"{stem}.html").write_text(template.replace("__CHART_DATA__", json.dumps(payload).replace("<", "\\u003c")))
    print(f'{payload["login"]}: {payload["total"]:,} {payload["kind"]}; outputs in {OUTPUT / stem}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", help="GitHub username (default: the active gh account)")
    parser.add_argument("--refresh", action="store_true", help="Fetch current counts using gh")
    parser.add_argument("--from-year", type=int)
    args = parser.parse_args()
    username = args.username or graphql("query { viewer { login } }")["data"]["viewer"]["login"]
    OUTPUT.mkdir(exist_ok=True)
    raw_path = OUTPUT / "github-contributions-raw.json"
    response = json.loads(raw_path.read_text()) if raw_path.exists() and not args.refresh else fetch(username)
    if (response.get("schemaVersion") != 4 or response["data"]["user"]["login"].lower() != username.lower()
            or response["commitsYtd"]["asOf"][:4] != str(datetime.now(timezone.utc).year)):
        response = fetch(username)
    raw_path.write_text(json.dumps(response, indent=2) + "\n")
    user = response["data"]["user"]
    monthly, as_of = aggregate(user, args.from_year or int(user["createdAt"][:4]), response["activity"])
    avatar_path = OUTPUT / f'{user["login"]}-avatar.png'
    if args.refresh or not avatar_path.exists():
        with urlopen(user["avatarUrl"], timeout=30) as stream:
            avatar_path.write_bytes(stream.read())
    import resvg_py
    original_uri = "data:image/png;base64," + base64.b64encode(avatar_path.read_bytes()).decode()
    thumbnail = resvg_py.svg_to_bytes(
        svg_string='<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="96" height="96">'
        + f'<image width="96" height="96" xlink:href="{original_uri}"/></svg>'
    )
    avatar_uri = "data:image/png;base64," + base64.b64encode(thumbnail).decode()
    base = {"login": user["login"], "url": user["url"], "avatar": avatar_uri}
    export_chart("github-activity", {**base, "kind": "activity", "monthly": monthly,
                 "asOf": as_of, "total": sum(row["count"] for row in monthly)})
    for key, stem in [("profile_contributions", "github-profile-contributions"), ("prs_closed", "github-prs-closed")]:
        series = [{"month": row["month"], "count": row[key]} for row in monthly]
        export_chart(stem, {**base, "kind": key, "monthly": series,
                     "asOf": as_of, "total": sum(row["count"] for row in series)})
    daily = daily_commit_counts(response["commitsYtd"])
    export_chart("github-commits-ytd", {**base, "kind": "commits", "daily": daily,
                 "asOf": response["commitsYtd"]["asOf"], "total": sum(row["count"] for row in daily)})
    contributions = daily_contribution_counts(response["contributionsYtd"])
    export_chart("github-contributions-ytd", {**base, "kind": "contributions", "daily": contributions,
                 "asOf": response["contributionsYtd"]["asOf"], "total": sum(row["count"] for row in contributions)})


if __name__ == "__main__":
    main()
