# GitHub contribution chart

Generate a monthly bar chart for any GitHub account. Export a PNG, an SVG, an interactive HTML chart, and a CSV.

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

The chart sums daily GitHub profile contributions into calendar months, starting with the account's creation year. The current month is partial.

Contributions include commits, pull requests, reviews, and other activity that GitHub counts on a profile. Private activity appears when GitHub exposes its aggregate counts to the authenticated account. The chart does not contain private repository names or commit contents.

Counts follow [GitHub's contribution criteria](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference). They can differ from a repository's commit count.

The files in `output/` show an example for `MrMarciaOng`. Generate your own chart to replace them. Raw daily data and downloaded avatars stay in the local cache and are ignored by Git.

## License

[MIT](LICENSE)
