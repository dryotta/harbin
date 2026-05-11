# Examples

Two sample fleets ship with harbin (overview §8). They live as
**standalone GitHub repos** and are checked out here as **git
submodules** so you can read the code without leaving the harbin
tree.

```bash
# After cloning harbin:
git submodule update --init --recursive

# Or in one go:
git clone --recurse-submodules https://github.com/dryotta/harbin
```

Their URLs are baked into `harbin sample-fleet add`:

```bash
harbin sample-fleet add news            # daily news brief
harbin sample-fleet add price-monitor   # hourly price check
```

The submodules are pinned to a specific commit on `main`; bumping a
sample is a deliberate `git submodule update --remote` + commit in
this repo.

---

## `harbin-agent-sample-news`

Path: [`harbin-agent-sample-news/`](harbin-agent-sample-news/) · URL:
<https://github.com/dryotta/harbin-agent-sample-news>

Demonstrates:

- A **cron-driven** task that fires daily at 07:00 local
  (`.harbin/schedule.yaml`).
- A **markdown artifact** (`briefs/brief-YYYY-MM-DD.md`) written into
  the fleet's `briefs/` directory.
- **Push-back to repo** (`artifact_policy.push_back: true`) so the
  archive accumulates over time.
- A per-fleet `agent_cli` override that runs `python agent/run.py`
  instead of the global `copilot` binary. The script is offline-
  deterministic by default; set `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`)
  to use a real LLM.

```
.harbin/
  fleet.yaml       # push_back: true; retain: 365d
  schedule.yaml    # 07:00 daily
.github/
  copilot-instructions.md
  agents/news-curator.md
skills/
  source-rules.md
agent/
  run.py           # offline-by-default brief generator
briefs/            # in-repo archive of all past briefs
```

Run it locally without harbin:

```bash
cd examples/harbin-agent-sample-news
HARBIN_PROMPT="default" HARBIN_ARTIFACT_DIR=./out python agent/run.py
```

---

## `harbin-agent-sample-price-monitor`

Path: [`harbin-agent-sample-price-monitor/`](harbin-agent-sample-price-monitor/) ·
URL: <https://github.com/dryotta/harbin-agent-sample-price-monitor>

Demonstrates:

- An **hourly cron** task.
- A **JSON artifact** (`prices.json`) and a per-day jsonl history file.
- **In-prompt tool use** — the agent reads `.harbin/watchlist.yaml`
  to know what to check.
- **Alert surfacing** — writes `alert.txt` when a threshold is
  crossed; the fleet's monitor row shows `⚠ 1 alert`.
- `push_back: false` (snapshots aren't interesting in git history).
- Optional real-API fetch via `HARBIN_PRICE_API_BASE` env var; default
  is offline-deterministic per-id pricing.

```
.harbin/
  fleet.yaml       # push_back: false; retain: 30d
  schedule.yaml    # hourly
  watchlist.yaml   # tickers + thresholds
.github/
  copilot-instructions.md
  agents/price-checker.md
skills/
  pricing-rules.md
agent/
  run.py           # snapshot + alert generator
history/           # per-day jsonl rollups
```

Run it locally without harbin:

```bash
cd examples/harbin-agent-sample-price-monitor
HARBIN_ARTIFACT_DIR=./out python agent/run.py
```
