# Architecture

How LeagueClipFarm works end to end: the data flow, the database, the background workers, the
HTTP API, and the invariants that must never be broken. Read this before changing anything
structural. For hands-on recipes (add a platform, add a metric, run tests), see
[DEVELOPMENT.md](DEVELOPMENT.md).

## Design principles (the invariants)

These are load-bearing. Every feature so far respects them; new features must too.

1. **Never delete a source recording.** Only Ascent deletes originals (its 50GB rollover). We
   delete only files *we* generated (re-encodes in `data/converted`, thumbnails) — see
   `jobs/cleanup.py`, whose docstring restates this rule.
2. **Never double-post content.** Publishing goes through the `published_content` ledger
   (`publishers/dedup.py` + the reserve/finalize/release functions in `db.py`). Any new code
   path that uploads MUST reserve content keys first. Duplicate uploads also risk YouTube
   demonetization, which defeats the whole project.
3. **`video_stats` is append-only.** It is the long-horizon performance history and survives
   source deletion and queue rebuilds. Never prune, rewrite, or key it by `media_files.id`
   (it's keyed by `(platform, platform_video_id)` on purpose).
4. **Degrade, don't block.** Every enrichment step (match data, thumbnails, editing, analytics)
   falls back gracefully: an unmatched clip still gets generic metadata, a failed edit falls
   back to plain vertical conversion, a missing analytics scope still yields basic stats.
   An upload should never be fully blocked by a nice-to-have step failing.
5. **Config split:** `config.yaml` = user-editable tunables; `.env` = secrets/seed values;
   `data/*.json` = runtime-editable state (accounts, FB creds, autopost settings) changed from
   the dashboard without restart. Code should read each from its proper layer.

## End-to-end data flow

```
Ascent records a game/clip into ~/Videos/Ascent[/Clips]
        │
        ▼
watcher/file_watcher.py      watchdog observer on both folders
watcher/ingest.py            waits until the file stops growing ("settled"), then ingests
        │
        ▼
jobs/pipeline.py             the enrichment pipeline for one media file:
  1. paths.py                  parse Ascent filename -> kind (clip|full_game) + recorded_at
  2. db.insert_media_file      row created with status 'processing'
  3. riot/match_matcher.py     find the Riot match this recording belongs to
       riot/client.py            Match-V5 via riotwatcher (account-scoped, key from riot_key.py)
       riot/timeline_analysis.py clips only: find the kill / multi-kill at the clip's offset
  4. riot/metadata_builder.py  title / description / hashtags / tags (CTR-optimized)
  5. media/thumbnail.py        composite thumbnail (media/composite.py + media/ddragon.py art)
  6. publishers/dedup.py       content_hash + highlight_ms stored for the dedup ledger
        │                      status -> 'ready' (or 'awaiting_match' if the game isn't
        │                      finished yet -- jobs/rematch.py retries it on a timer)
        ▼
review/ dashboard (http://localhost:8000)
  user reviews/edits drafts, picks platforms, hits Approve
  (or jobs/scheduler.py auto-posts the best eligible clip on its hour slots)
        │
        ▼
jobs/publish_job.py          per selected platform:
  1. db.try_reserve_content    atomically claim the content keys (duplicate -> hard stop)
  2. media/edit.py             clips: 9:16 + hook caption + music + normalize + fades
  3. publishers/registry.py    routes to publishers/youtube.py | publishers/facebook.py
  4. db.finalize_reserved_content / release_reserved_content (success / failure)
  5. jobs/cleanup.purge_upload_cache   delete our re-encode once published everywhere
        │
        ▼
metrics/poller.py            hourly: fetch stats for every published video
  metrics/youtube_stats.py     Data API statistics + Analytics API (watch time, retention, subs)
  metrics/facebook_stats.py    Graph API fields + video_insights (Reel vs regular families)
  db.insert_video_stats        append-only snapshot into video_stats
        │
        ▼
metrics/analysis.py          strong/weak classification, per-platform summary, insights,
                             cumulative growth timeline -> /api/metrics -> /metrics page
```

## Module map

| Module | Responsibility |
|---|---|
| `clipfarm/paths.py` | Ascent's on-disk layout + filename convention (`MM-DD-YYYY-HH-MM[-SS]`); classifies clip vs full game by folder |
| `clipfarm/config.py` | Typed settings: `config.yaml` overlaid with `.env` (`Secrets`). Relative paths anchored to project root |
| `clipfarm/db.py` | The entire SQLite layer: schema, idempotent column migrations, and every query. No other module writes SQL |
| `clipfarm/accounts.py` | Multi-account store (`data/accounts.json`); each recording is tagged with the account that made it |
| `clipfarm/riot_key.py` | Runtime-editable Riot API key (`data/riot_key.txt` overrides `.env`; the key expires ~daily) |
| `clipfarm/fb_settings.py` | Runtime-editable Facebook credentials (`data/facebook.json` overrides `.env`) |
| `clipfarm/autopost.py` | Runtime-editable auto-post settings (`data/autopost.json`), normalized on load/save |
| `clipfarm/watcher/` | watchdog observer + settle-detection ingest (a recording is ingested only once it stops growing) |
| `clipfarm/riot/` | Match-V5 client (riotwatcher), filename→match matcher, timeline kill-cluster analysis, metadata builder, champion name mapping |
| `clipfarm/media/` | ffmpeg wrapper, frame grabs, composite thumbnails (Data Dragon art + Anton font), clip editing (vertical/music/caption/fades in one encode) |
| `clipfarm/jobs/` | `pipeline` (ingest→enrich), `rematch` (retry unmatched), `publish_job` (dedup-guarded upload), `scheduler` (auto-post cadence), `scan` (manual/auto folder rescan), `cleanup` (file lifecycle), `models` (shared dataclasses) |
| `clipfarm/publishers/` | `registry` (platform plug-in point), `youtube`, `facebook`, `dedup` (content signatures), `quota` (self-tracked daily upload quota) |
| `clipfarm/metrics/` | `base` (provider plug-in point), `youtube_stats`, `facebook_stats`, `poller` (hourly snapshots), `analysis` (classification/insights/timeline + 24h velocity and title-A/B scoring from the time-series) |
| `clipfarm/review/` | FastAPI app (`app.py` also starts all background workers), `routes.py` (every endpoint), vanilla-JS dashboard (`templates/` + `static/`, no build step) |
| `clipfarm/cli.py` | All entry points (`watch`, `backfill`, `reauth-*`, `check-*`, `refresh-drafts`, `backpost-facebook`, account commands) |
| `scripts/` | `backfill_existing` (process what's already on disk), `reauth_youtube`, `reauth_facebook` (interactive token minting) |

## Database schema (`clipfarm/db.py`)

SQLite at `data/clipfarm.sqlite3`. Schema is created idempotently on startup; columns added
after first release live in `_MIGRATION_COLUMNS` and are `ALTER TABLE`-d in if missing —
**that list is the migration mechanism; never edit `_SCHEMA`'s existing tables in place.**

| Table | What it holds | Lifecycle |
|---|---|---|
| `media_files` | One row per recording: kind, `recorded_at`, match enrichment (champion/KDA/role/rank/patch/opponent), draft metadata, `title_variant` (which A/B hook style the title used), `content_hash` + `highlight_ms` (dedup), `source_deleted_at` (archived), `published_at` | Rows are **never deleted**, only archived (`source_deleted_at` set) when Ascent removes the file |
| `publish_targets` | Per (media_file, platform): selected?, status `pending→uploading→published/failed`, `platform_video_id`, error | Unique on `(media_file_id, platform)` |
| `published_content` | **The dedup ledger.** `(platform, content_key)` primary key; content keys are `file:<hash>`, `match:<id>`, `clip:<id>:<ms>` | Permanent. Reservation rows (no `platform_video_id` yet) are released on upload failure |
| `autopost_log` | One row per scheduler firing, per track (`clip`/`full_game`) | Drives daily cap, per-game cap, min-gap spacing |
| `video_stats` | **Append-only time series** keyed by `(platform, platform_video_id)`: views/likes/comments/shares, watch time, retention, plus `extra_json` for anything without a column | Never pruned; survives source deletion and queue rebuilds |
| `quota_usage` | Our own count of uploads per platform per day (YouTube's `videos.insert` bucket is 100/day) | Success-only counting |

`extra_json` is the escape hatch: a new metric needs **no migration** — unknown keys are stored
as JSON and `analysis.py` parses out the ones it knows (`subscribers_gained`, `reel_plays`).

## Background workers

All four start in `review/app.py`'s FastAPI lifespan (i.e. under `clipfarm.cli watch`). Each is
a daemon thread with a catch-all so one bad pass never kills the loop.

| Worker | Module | Interval | Job |
|---|---|---|---|
| Rematch sweep | `jobs/rematch.py` | `riot.rematch_interval_seconds` (180s) | Retry `awaiting_match` items (their game wasn't in Match-V5 yet); give up after `rematch_give_up_hours` (48h) → generic metadata |
| Auto-post scheduler | `jobs/scheduler.py` | ~1 min tick | Fire the best eligible clip on each configured hour slot (quality gate, per-game cap, min-gap); separate full-game track; backfills failed auto-posts via `db.failed_autopost_targets` |
| Reconcile sweep | `jobs/cleanup.py` | 900s | Archive rows whose source Ascent deleted + remove our derived files. Skips the pass entirely if the Ascent folders are missing (drive offline guard) |
| Stats poller | `metrics/poller.py` | 3600s | Snapshot stats for every published video via the provider registry; one platform failing doesn't stop the others |

## HTTP API (`review/routes.py`)

The dashboard is the only client; everything is JSON unless noted.

| Endpoint | Purpose |
|---|---|
| `GET /api/queue`, `GET /api/queue/{id}` | Review queue (archived rows excluded) with per-platform targets + duplicate warnings |
| `POST /api/queue/{id}/approve` | Save edits + publish to the selected platforms |
| `POST /api/queue/{id}/retry/{platform}` | Retry one failed platform |
| `POST /api/queue/{id}/regenerate-thumbnail` | Re-grab the thumbnail frame (optional `offset_seconds`) |
| `GET/POST /api/autopost`, `POST /api/autopost/apply-recommended` | Scheduler settings; apply the best-time-to-post hours |
| `GET /api/metrics`, `POST /api/metrics/refresh` | Full analysis payload (summary/videos/insights/timeline); force an immediate poll |
| `GET /api/quota` | Today's self-tracked upload counts |
| `POST /api/rematch`, `GET /api/rematch/pending` | Manual rematch pass; how many are waiting |
| `GET /api/riot-health`, `POST /api/riot-key` | Riot key validity; hot-swap the key (no restart) |
| `GET/POST /api/facebook` | Facebook creds panel (validated live) |
| `GET /api/accounts`, `POST /api/accounts/switch` | Account list; validated switch (triggers a background scan) |
| `POST /api/scan` | Manual Ascent folder rescan |
| `GET /media/{id}/video`, `GET /media/{id}/thumbnail` | File responses for the dashboard player/preview |
| `GET /`, `GET /metrics` | The two pages (static HTML + vanilla JS; `Cache-Control: no-store` so edits show up on refresh) |

## External APIs and their sharp edges

Hard-won knowledge; ignoring these costs hours.

- **Riot:** dev API keys expire ~every 24h (`check-riot-key`, or the dashboard banner + hot-swap).
  The Match-V5 history `startTime/endTime` filter matches on a game's **end** timestamp, not its
  start — `full_game_max_duration_minutes` exists to compensate. Mid-game clips can't match until
  the game finishes → the rematch sweep.
- **YouTube:** `videos.insert` has a dedicated **100 uploads/day** bucket (tracked ourselves in
  `quota_usage` to fail with a clear message instead of a raw 403). The on-demand Analytics API
  (`reports.query`) does **not** expose `impressions`/`impressionClickThroughRate` ("Unknown
  identifier") — those exist only in Studio UI and the bulk Reporting API, so we track retention
  (`averageViewPercentage`) and `subscribersGained` instead. Analytics needs the
  `yt-analytics.readonly` scope (`reauth-youtube`) **and** the YouTube Analytics API enabled on
  the Cloud project.
- **Facebook:** Reels and regular Page videos expose **different insight metric families**, and
  one invalid metric in a request 400s the whole call — `facebook_stats.py` requests
  `_REEL_METRICS` and `_VIDEO_METRICS` separately and merges. `read_insights` permission is
  required for any insights (`reauth-facebook` mints a durable Page token that carries it).
  Resumable (chunked) upload must POST chunks to `/{page-id}/videos` with `upload_session_id`
  in the body — POSTing to `/{upload_session_id}` fails with "(#100) target is required". Caps:
  45 min / 1.5 GB per video, ~30 Reels per 24h.
- **Data Dragon** (champion art) is unauthenticated and cached in `data/ddragon/`; thumbnail
  generation degrades to a plain frame grab if it's unreachable.

## Extension points

Two registries make the common extensions one-file changes (full recipes in
[DEVELOPMENT.md](DEVELOPMENT.md)):

- **New publishing platform** → `clipfarm/publishers/registry.py`: write
  `publish(media_file) -> platform_video_id`, add a `Platform(...)` entry. Dashboard checkboxes,
  Approve, status chips, Retry, and backfill-on-enable all follow automatically.
- **New stats source** → `clipfarm/metrics/base.py`: write
  `fetch(video_ids) -> {id: {metric: value}}`, register a `StatsProvider`, import the module in
  `metrics/poller.py`. Unknown metrics land in `extra_json` — no schema change.

## Runtime state layout (`data/`, gitignored)

```
data/
  clipfarm.sqlite3      the database (schema above)
  accounts.json         Riot accounts + which is active
  autopost.json         scheduler settings
  facebook.json         FB Page ID + token (overrides .env)
  riot_key.txt          hot-swapped Riot key (overrides .env)
  client_secret.json    Google OAuth client (downloaded from Cloud console)
  youtube_token.json    cached YouTube OAuth token (upload + analytics scopes)
  converted/            our 9:16 re-encodes awaiting upload (purged after publish)
  thumbnails/           generated thumbnails (removed when a row is archived)
  ddragon/              champion art cache
  logs/clipfarm.log     rotating-ish app log (also mirrored to console)
```
