# Development guide

Everything needed to work on LeagueClipFarm productively. Companion to
[ARCHITECTURE.md](ARCHITECTURE.md) (read that first for the big picture and the invariants).

## Setup

```powershell
python -m venv .venv            # Python 3.11+
.venv\Scripts\activate
pip install -e ".[dev]"         # runtime deps + pytest + ruff
copy .env.example .env          # then fill in at least the RIOT_* values
```

Run everything with **the venv's Python** (`.venv\Scripts\python.exe`) — the system Python
won't have the dependencies.

| Command | What |
|---|---|
| `python -m clipfarm.cli watch` | Watcher + dashboard at http://localhost:8000 (the normal way to run) |
| `pytest` | Full test suite (~2s, no network, no credentials) |
| `ruff check .` | Lint (config in `pyproject.toml`) |
| `python -m clipfarm.cli --help` | All CLI commands |

To iterate on the dashboard UI without the watcher, run uvicorn directly:

```powershell
.venv\Scripts\python.exe -m uvicorn clipfarm.review.app:app --port 8010
```

The app serves HTML/JS/CSS with `Cache-Control: no-store`, so a browser refresh always picks up
your edits — no build step, no bundler, plain refresh.

## Testing conventions

- **No network, no credentials, no real DB.** Tests monkeypatch `db.get_settings` to a tmp-path
  SQLite file (see the `temp_db` fixture pattern in `tests/test_analysis.py`) and fake HTTP with
  stub objects (see `tests/test_facebook_stats.py`). Riot data comes from
  `tests/fixtures/sample_match_detail.json` / `sample_timeline.json`.
- **One test file per module** (`test_analysis.py` ↔ `metrics/analysis.py`), with a module
  docstring saying what behavior the file locks down.
- Test the **behavior contract**, not internals: e.g. "a 403 keeps basic stats" rather than
  asserting on private helpers.
- When you fix a bug, add the test that would have caught it (the suite is the record of every
  API sharp edge we've hit — e.g. `test_youtube_errors.py`, `test_publish_dedup.py`).

## Code style

- Python 3.11+, `from __future__ import annotations`, modern unions (`str | None`).
- Every module starts with a docstring saying what it owns; comments explain **why**
  (API quirks, product reasoning), not what.
- 120-char lines. `ruff check .` must pass; rule choices live in `[tool.ruff]` in
  `pyproject.toml`.
- All SQL lives in `clipfarm/db.py`. Route handlers and jobs call `db.*` functions — don't
  scatter queries. (One deliberate exception: one-off CLI batch queries in `cli.py`.)
- Frontend is vanilla JS + inline SVG, no dependencies — keep it that way (it must work
  offline/CSP-clean, and there is no build pipeline).
- Background loops: daemon thread, `while True: sleep(...)`, catch-all `except Exception:
  logger.exception(...)` so one bad pass never kills the worker.

## Recipes

### Add a publishing platform (e.g. TikTok)

1. Create `clipfarm/publishers/tiktok.py` exposing `publish(media_file: sqlite3.Row) -> str`
   (return the platform video id; raise with a *human-readable* message on failure — copy the
   error-mapping pattern from `youtube.py`, including success-only quota counting if the
   platform has an upload cap).
2. Add a `Platform(...)` entry to `_REGISTRY` in `clipfarm/publishers/registry.py` — name,
   which kinds it takes (`"clip"`, `"full_game"`), and an `is_enabled` callable (config flag or
   credential check).
3. That's it for publishing: dashboard checkbox, Approve, status chips, Retry, and
   backfill-on-enable all work automatically. Add `tests/test_tiktok.py` with a stubbed HTTP
   layer.
4. (Optional) stats: see the next recipe so the metrics page covers it too.

### Add a metrics/stats provider

1. Create `clipfarm/metrics/<platform>_stats.py` with
   `fetch(video_ids: list[str]) -> dict[str, dict]` mapping each video id to
   `{metric_name: value}`. Use the column names in `db.insert_video_stats`
   (`views`, `likes`, `comments`, `shares`, `watch_time_minutes`, `avg_view_seconds`,
   `avg_view_pct`, `impressions`, `ctr`) where they fit; anything else is auto-stored in
   `extra_json` — **no schema change needed**.
2. Register at module bottom:
   `base.register(base.StatsProvider(platform="<platform>", fetch=fetch))`.
3. Import the module in `clipfarm/metrics/poller.py` (the self-registration import line).
4. Degrade per-metric: if an analytics permission is missing, still return the basic numbers
   (see `facebook_stats.py` for the pattern, including the Reels-vs-video metric families).
5. To surface a new `extra_json` metric in the dashboard, teach
   `clipfarm/metrics/analysis.py::video_records()` to parse it, then render it in
   `review/static/metrics.js`.

### Add a config option

- **User tunable** → add a field (with default + a *why* comment) to the right `BaseModel` in
  `clipfarm/config.py`, then document it in `config.yaml`. Existing configs keep working
  because every section is loaded with `raw.get(..., {})` and field defaults.
- **Secret** → add to `Secrets` in `config.py` **and** to `.env.example` with a comment.
- **Runtime-editable (dashboard, no restart)** → follow the `autopost.py` /`fb_settings.py`
  pattern: a dataclass + `data/<name>.json` store + GET/POST endpoints in `routes.py`.

### Add a database column

Append a `("table", "column", "TYPE")` tuple to `_MIGRATION_COLUMNS` in `clipfarm/db.py`.
It's applied idempotently on startup. **Don't** edit existing tables in `_SCHEMA` —
`CREATE TABLE IF NOT EXISTS` won't alter live databases. For new *metrics* specifically,
prefer `extra_json` (zero migration) unless the column needs SQL-side filtering/sorting.

### Add a background worker

Copy the shape of `jobs/cleanup.py::start_reconcile_sweep` (daemon thread, interval sleep,
catch-all logging, a lock if the job can also be triggered from a route), and start it in
`review/app.py::_lifespan` next to the other four.

### Add a dashboard panel or endpoint

Routes live in `review/routes.py` (keep handlers thin — real logic goes in the owning module).
UI is `review/templates/*.html` + `review/static/*.js`. `dashboard.js` polls `/api/queue` every
few seconds; `metrics.js` loads `/api/metrics` and re-renders — follow the existing
`STATE`-object + render-function style.

## Gotchas (cost real hours; don't rediscover)

- **Riot dev keys expire ~every 24h.** The dashboard shows a banner; hot-swap via the header
  panel or `POST /api/riot-key` — no restart. `python -m clipfarm.cli check-riot-key` to check.
- **Riot match-history time filter matches on game END, not start** — that's why
  `full_game_max_duration_minutes` exists. Don't "simplify" it away.
- **YouTube impressions/CTR are not in the on-demand Analytics API** — only Studio UI + bulk
  Reporting API. Don't re-add them to `youtube_stats.py`; every poll would waste a failing call.
- **Facebook: never mix Reel and regular-video insight metrics in one request** — one unknown
  metric 400s the entire call. Keep the two families separate (`_REEL_METRICS` /
  `_VIDEO_METRICS` in `facebook_stats.py`).
- **Facebook chunked upload:** POST chunks to `/{page-id}/videos` with `upload_session_id` in
  the body, *not* to `/{upload_session_id}`. Resumable cap: 45 min / 1.5 GB. Reels: ~30/24h.
- **YouTube uploads: 100/day hard bucket** for `videos.insert` (separate from the 10k-unit
  pool). We count our own usage in `quota_usage` to stop early with a clear message.
- **Windows paths**: the repo pins absolute Ascent/ffmpeg paths in `config.yaml`; tests never
  touch them. Use `pathlib` everywhere; never assume `/` separators in comparisons — compare
  `Path.resolve()` results (see `paths.classify_media`).
- **`media_files` rows are never deleted** — archiving sets `source_deleted_at`. Queries that
  should exclude archived items must filter on it (see `db.list_queue`).
- **Dashboard JS caching**: served with `no-store` on purpose; if you remove that middleware,
  stale JS makes new buttons silently dead.

## Project status / near-term roadmap

Semi-finished and in daily use. The pipeline, dedup, scheduler, metrics, and both platform
integrations are complete and tested. Known open threads (discussed, not built):

- **TikTok publisher** (Phase 4): registry + config stubs exist; needs `publishers/tiktok.py`,
  OAuth flow, and TikTok's app audit for public posting.
- **Study the 2026-07-09 near-viral clip** (29k views @ 76% retention) and the retention
  leaders to define a repeatable content formula.
- **Auto-trim clips >60s** so they qualify as Shorts.
- **A/B title rotation**, **per-champion daily variety cap**, **follower-conversion end-card**.

The metrics dashboard's insight so far: retention is the lever that converts views into
subscribers; clips vastly outperform full games; multi-kill ADC clips are the strongest bucket.
