#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["resvg-py==0.5.0"]
# ///
"""Generate a monthly GitHub contribution chart using the active gh login."""

import argparse
import base64
from collections import defaultdict
import csv
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import subprocess
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"


def graphql(query):
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        check=True, capture_output=True, text=True,
    )
    response = json.loads(result.stdout)
    if response.get("errors"):
        raise RuntimeError(response["errors"])
    return response


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
    return graphql(
        "query { user(login:" + json.dumps(username)
        + ") { login name url avatarUrl createdAt " + " ".join(fields) + " } }"
    )


def aggregate(user, from_year):
    months = defaultdict(int)
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
                months[date[:7]] += day["contributionCount"]
    if not months:
        raise ValueError("No data exists for the selected date range")
    return [{"month": month, "count": months[month]} for month in sorted(months)], max(ends)


def svg_chart(user, monthly, avatar_uri, as_of):
    total = sum(row["count"] for row in monthly)
    max_count = max(row["count"] for row in monthly)
    ceiling = max(5, ((max_count + 99) // 100) * 100)
    left, right, top, bottom = 36, 792, 112, 282
    step = (right - left) / len(monthly)
    date_label = lambda month: datetime.strptime(month, "%Y-%m").strftime("%b %Y")
    date_range = f'{date_label(monthly[0]["month"])} – {date_label(monthly[-1]["month"])}'
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="900" height="370" viewBox="0 0 900 370" role="img">',
        f'<title>{escape(user["login"])}: {total:,} GitHub contributions</title>',
        '<desc>Monthly GitHub profile contributions, including private activity. The latest month is partial.</desc>',
        '<rect x="1" y="1" width="898" height="368" rx="9" fill="#ffffff" stroke="#d1d9e0" stroke-width="1.5"/>',
        '<defs><clipPath id="avatar"><circle cx="61" cy="56" r="27"/></clipPath></defs>',
        f'<image x="34" y="29" width="54" height="54" xlink:href="{avatar_uri}" clip-path="url(#avatar)"/>',
        '<g font-family="Arial, Helvetica, sans-serif">',
        f'<text x="104" y="51" font-size="21" font-weight="600" fill="#0969da">{escape(user["login"])}</text>',
        f'<text x="104" y="77" font-size="17" fill="#59636e">{total:,} contributions</text>',
        f'<text x="862" y="53" text-anchor="end" font-size="14" fill="#59636e">{date_range}</text>',
    ]
    for i in range(4):
        count = ceiling * i / 3
        y = bottom - (bottom - top) * count / ceiling
        pieces.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="#e3e8ed" stroke-dasharray="4 4"/>')
        pieces.append(f'<text x="802" y="{y + 5:.2f}" font-size="14" fill="#59636e">{count:,.0f}</text>')
    pieces.append('<text x="873" y="197" transform="rotate(90 873 197)" text-anchor="middle" font-size="15" fill="#59636e">Contributions</text>')
    tick_indices = [i for i, row in enumerate(monthly)
                    if row["month"].endswith("-01") and (int(row["month"][:4]) - int(monthly[0]["month"][:4])) % 2 == 0]
    for index in tick_indices:
        x = left + step * (index + .5)
        pieces.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="#e3e8ed" stroke-dasharray="4 4"/>')
        pieces.append(f'<text x="{x:.2f}" y="312" text-anchor="{"start" if index == 0 else "middle"}" font-size="14" fill="#59636e">{date_label(monthly[index]["month"])}</text>')
    for i, row in enumerate(monthly):
        x = left + step * i + .6
        height = (bottom - top) * row["count"] / ceiling
        pieces.append(f'<rect x="{x:.2f}" y="{bottom-height:.2f}" width="{max(.2, step-1.2):.2f}" height="{height:.2f}" fill="#388bfd" opacity="{.65 if i == len(monthly)-1 else 1}"/>')
    peak_index = max(range(len(monthly)), key=lambda i: monthly[i]["count"])
    peak_x = left + step * (peak_index + .5)
    peak_y = bottom - (bottom - top) * max_count / ceiling
    pieces.append(f'<text x="{peak_x:.2f}" y="{peak_y-10:.2f}" text-anchor="middle" font-size="13" fill="#1f2328">{max_count:,}</text>')
    pieces.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#d1d9e0"/>')
    pieces.append(f'<text x="{right}" y="312" text-anchor="end" font-size="14" fill="#59636e">{date_label(monthly[-1]["month"])}</text>')
    stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00")).strftime("%d %b %Y").lstrip("0")
    pieces.append(f'<text x="36" y="346" font-size="12" fill="#59636e">Monthly contributions · includes private activity · latest month partial · as of {stamp}</text>')
    pieces.append('</g></svg>')
    return "\n".join(pieces)


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
    if response["data"]["user"]["login"].lower() != username.lower():
        response = fetch(username)
    raw_path.write_text(json.dumps(response, indent=2) + "\n")
    user = response["data"]["user"]
    monthly, as_of = aggregate(user, args.from_year or int(user["createdAt"][:4]))
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
    svg = svg_chart(user, monthly, avatar_uri, as_of)
    (OUTPUT / "github-activity.svg").write_text(svg + "\n")
    (OUTPUT / "github-activity.png").write_bytes(resvg_py.svg_to_bytes(svg_string=svg, width=2400))
    with (OUTPUT / "github-activity.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["month", "count"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(monthly)
    payload = {"login": user["login"], "url": user["url"], "avatar": avatar_uri,
               "monthly": monthly, "asOf": as_of, "total": sum(row["count"] for row in monthly)}
    (OUTPUT / "github-activity-data.json").write_text(json.dumps(payload, indent=2) + "\n")
    template = (ROOT / "chart-template.html").read_text()
    html = template.replace("__CHART_DATA__", json.dumps(payload).replace("<", "\\u003c"))
    (OUTPUT / "github-activity.html").write_text(html)
    print(f'{user["login"]}: {payload["total"]:,} contributions across {len(monthly)} months; outputs in {OUTPUT}')


if __name__ == "__main__":
    main()
