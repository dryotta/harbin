# Examples

The two sample fleets that ship with harbin (overview §8) live as
**standalone GitHub repos**, not vendored into this source tree. Their
URLs are baked into `harbin sample-fleet add`:

```bash
harbin sample-fleet add news            # daily news brief
harbin sample-fleet add price-monitor   # hourly price check
```

This directory documents each sample in prose so contributors can
understand the feature surface each exercises without leaving the
harbin repo.

---

## `harbin-agent-sample-news`

URL: <https://github.com/dryotta/harbin-agent-sample-news>

Demonstrates:

- A **cron-driven** task that fires daily at 07:00 local.
- A **markdown artifact** (`brief-YYYY-MM-DD.md`) written into the
  fleet's `briefs/` directory.
- **Push-back to repo** (`artifact_policy.push_back: true`) so the
  archive accumulates over time.

```
.harbin/
  fleet.yaml       # push_back: true; retain: 365d
  schedule.yaml    # 07:00 daily
.github/
  copilot-instructions.md
  agents/news-curator.md
skills/
  source-rules.md
```

---

## `harbin-agent-sample-price-monitor`

URL: <https://github.com/dryotta/harbin-agent-sample-price-monitor>

Demonstrates:

- An **hourly cron** task.
- A **JSON artifact** (`prices.json`) and diff history files.
- **In-prompt tool use** — the agent reads `watchlist.yaml` to know
  what to check.
- **Alert surfacing** — writes `alert.txt` when a threshold is
  crossed; the fleet's monitor row shows `⚠ 1 alert`.

```
.harbin/
  fleet.yaml       # push_back: false; retain: 30d
  schedule.yaml    # hourly
  watchlist.yaml   # tickers + thresholds
.github/
  copilot-instructions.md
  agents/price-checker.md
```
