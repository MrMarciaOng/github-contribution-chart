# GitHub contribution chart

Generate a monthly activity chart for any GitHub account, including comments and closed pull requests. Export a PNG, an SVG, an interactive HTML chart, and a CSV.

![Example chart for MrMarciaOng](output/github-activity.png)

## Generate your chart

Install [GitHub CLI](https://cli.github.com/) and [uv](https://docs.astral.sh/uv/), then run:

```sh
gh auth login
gh repo clone MrMarciaOng/github-contribution-chart
cd github-contribution-chart
uv run generate_chart.py --refresh
```

The generator uses your active GitHub CLI account. `uv` installs Python and the pinned rendering dependency as needed.

To generate a chart for another account, pass its username:

```sh
uv run generate_chart.py --username octocat --refresh
```

To limit the chart to activity since a particular year, add `--from-year`:

```sh
uv run generate_chart.py --from-year 2023 --refresh
```

Open `output/github-activity.png` for the image or `output/github-activity.html` for monthly values on hover or tap.

To regenerate the chart from cached data, omit `--refresh`:

```sh
uv run generate_chart.py
```

## Read the totals

The chart combines these counts for each month, starting with the account's creation year:

- GitHub profile contributions, including commits, opened pull requests, reviews, and other activity that GitHub counts on a profile.
- Issue and PR conversation comments, plus direct commit comments, counted on their creation dates in UTC.
- PRs authored by the target account that are currently closed or merged, counted on their latest closure dates in UTC. A merged PR counts as one closure.

The current month is partial. A PR can contribute once when opened and once when closed. These represent separate activities. The combined total is a custom activity count.

The CSV includes `count`, `profile_contributions`, `comments`, and `prs_closed` columns. The HTML chart shows the monthly breakdown on hover or tap.

Profile counts follow [GitHub's contribution criteria](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference). Inline review comments, discussion comments, deleted comments, and earlier close/reopen cycles are outside the extra comment and closure counts. Review submissions already contribute to the profile count.

Private profile activity appears when GitHub exposes its aggregate counts. Extra comments and closures include only objects visible to the authenticated account, so access restrictions can leave gaps. The generator paginates through the historical connections. It does not use the recent-events feed.

The exports contain monthly counts, with no private repository names, comment text, or PR contents.

The files in `output/` show an example for `MrMarciaOng`. Generate your own chart to replace them. Raw daily data, activity IDs and timestamps, and downloaded avatars stay in the local cache and are ignored by Git.

## Run checks

```sh
python3 -m unittest -v
```

## License

[MIT](LICENSE)
