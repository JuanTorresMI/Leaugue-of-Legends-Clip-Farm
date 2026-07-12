# LeagueClipFarm

Ingests Ascent recordings/clips, enriches them with real Riot match data, generates high-CTR
titles/thumbnails, edits clips for watchability, and stages everything in a live local dashboard
for one-click cross-posting to YouTube and Facebook.

**Pipeline:** Ascent records a game/clip → the file watcher ingests it → Riot Match-V5 (+ the
timeline for clips) resolves the exact champion/KDA/rank/matchup or multi-kill → a discoverability
title, description, tags, and composite thumbnail are generated → it appears in the dashboard →
you review/edit and Approve → it publishes to every enabled platform (clips as YouTube Shorts +
Facebook Reels, full games as long-form + Page videos).

**Live dashboard:** updates without refresh — new recordings appear as you play, awaiting-match
items fill in when their game finishes, and per-platform status chips + Retry track each upload.
Riot key, active account, and Facebook credentials are all editable in the dashboard (no restart).

**Everything runs from one command:** `python -m clipfarm.cli watch`.

## Never double-posts the same video

Every upload is checked against a permanent **published-content ledger** keyed by a *content
signature*, not a file path — so the same video can't go out twice even if it's re-recorded,
renamed, re-ingested, or the queue DB is rebuilt. Three signatures are checked (a hit on any
one blocks the upload):
- `file:<hash>` — the file's byte content (catches renamed/re-ingested copies)
- `match:<id>` — a full game (one upload per match per platform)
- `clip:<id>:<ms>` — a specific kill in a game (different kills from one game still post; a
  re-post of the *same* moment is blocked)

The dashboard shows a **⚠ Already published to …** warning on any queue item whose content
already went out, and publishing hard-blocks it (the target shows a `duplicate` chip) so it
can never slip through by a double-click, retry, or re-approve. This also protects
monetization: YouTube penalizes reused/duplicate content.

## Performance metrics dashboard

A second page at **http://localhost:8000/metrics** (linked from the header) tracks how every
uploaded video performs. A background poller snapshots stats hourly into an **append-only
time-series** table keyed by the platform's video id, so **the data survives the source recording
being deleted** and accumulates indefinitely — the longer it runs, the richer the history.

The page has three tabs:
- **Overview** — channel totals (views, watch time, retention, subs gained, **views gained in the
  last 24h**), a **cumulative-views growth chart** that builds up over time, a **🚀 Rising now**
  panel (the biggest 24h gainers — the earliest visible sign of a clip taking off), a **🧪 Title
  styles** panel scoring the A/B title experiment, a **strong / average / weak** performance mix,
  and a "what's working" panel: strongest champions, clips-vs-full-games, **retention leaders**,
  and **subscriber magnets** (the clips actually converting followers).
- **YouTube** / **Facebook** — each platform gets its own totals and a **sortable** video table
  (click any column), including a **24h** view-delta column. Every video is tagged **🔥 strong /
  ➖ average / ⚠️ weak** relative to that platform's own median views (with a retention bump), so
  weak content is obvious at a glance.

The strong/weak classification and all rollups live on the backend (`clipfarm/metrics/analysis.py`)
so they're reusable and tested, not just UI sugar.

- **Basic stats** (views, likes, comments) work with your existing YouTube/Facebook authorization.
- **YouTube full analytics** (watch time, average view %, shares, and **subscribers gained** — the
  real follower-conversion signal) need the read-only analytics scope — run
  `python -m clipfarm.cli reauth-youtube` **once** to grant it (the scope is already requested;
  uploads and basic stats keep working until you do). You must also enable the **YouTube Analytics
  API** for your Google Cloud project (one click in the Cloud console). Note: impressions & CTR are
  **not** available from the on-demand Analytics API (they live only in YouTube Studio's UI and the
  bulk Reporting API), so the dashboard tracks retention and sub-conversion instead.
- **Facebook insights** (Reel plays, watch time, average time watched — regular Page videos also
  report impressions) need the `read_insights` permission — run
  `python -m clipfarm.cli reauth-facebook` **once** to mint a durable Page token that carries it.
  The command exchanges a short-lived user token for a long-lived one and reads your Page's
  non-expiring token; it needs `FB_APP_ID` / `FB_APP_SECRET` in `.env` (or prompts). Clips post as
  Reels and full games as regular videos, which expose different insight metrics — the provider
  requests each family separately so both get analytics.
- **Best time to post**: the page ranks your average views by local upload hour and, once there's
  enough data, offers a one-click **"apply recommended hours to auto-post."**

Adding another platform's stats is one file: implement `fetch(video_ids) -> {id: {metric: value}}`
and register a `StatsProvider` in `clipfarm/metrics/` (mirrors the publisher registry). Unknown
metrics are stored as JSON, so new numbers need no schema change.

## Storage: keeps the data, not the bytes

Ascent deletes recordings as it rolls past 50GB. The app never deletes your originals, but it
keeps its own footprint small **without losing anything needed for analysis**:
- After an item is published everywhere, its big regenerated upload file (the vertical re-encode)
  is deleted — it's reproducible and only existed to upload.
- When Ascent removes a source recording, that item is **archived**: it leaves the review panel
  and its leftover thumbnails are cleaned up, but the DB row + video ids + all performance stats
  stay for the metrics dashboard. A guarded background sweep handles this (and won't mass-archive
  if the Ascent drive is briefly offline).

## Auto-scan on account switch

Switching accounts in the dashboard kicks off a background scan of the Ascent folders +
re-match, so a new account's clips get ingested and tagged automatically. There's also a manual
`POST /api/scan`.

**Self-healing for wrong-account recordings.** If a game or clip was recorded while the *wrong*
account was selected, it gets tagged to that account and its Riot match never resolves (that
account never played the game), leaving it with generic metadata and a plain thumbnail.
Switching to the correct account now runs a **claim pass** that re-attempts those stranded
recordings against the now-active account and **adopts the ones that actually match** — re-tagging
them to the account that truly owns the game and filling in the real title/thumbnail. It only
persists a recording on a genuine match, so it never steals another account's clips, and a
per-recording marker means repeated switches don't re-hit the Riot API for the same misses.

## Auto-post scheduler (drip, don't dump)

Toggle **Auto-post** in the dashboard header to have the app publish your best clips on a
schedule instead of you approving each one. It's built around what actually grows a Shorts
channel rather than raw volume:
- **Spaced cadence, not bursts.** One post per scheduled hour-slot (default `12, 17, 21` local —
  the midday + evening viewing peaks), so same-day posts don't cannibalize each other. A hard
  min-gap means a post-downtime catch-up drips instead of dumping. 1–3/day is the sweet spot;
  add/remove hours to change the daily count.
- **Quality gate.** No-kill "highlight" clips are skipped; `min_kill_streak` sets the floor
  (1 = allow solo kills, 2 = doubles and up only).
- **Per-game cap.** At most N clips from one match (default 3), so a single game can't flood the
  feed with near-identical clips.
- **Best first.** Each slot fires the highest-value eligible clip (pentakill > quadra > … > solo).

Everything is editable from the header panel (enable, post hours, min streak, per-game cap) and
persists to `data/autopost.json`. Only the current account's clips are considered, and the
publish-time dedup ledger guarantees it can never double-post. Leave it off to keep the original
review-and-Approve flow.

**Long-form auto-upload:** full games have their own separate track (toggle + hours in the same
panel), defaulting to one mid-afternoon slot — long-form and Shorts peak at opposite times, and
1–3 long videos/week outperforms daily. It's off by default.

**Missed uploads are backfilled:** if an auto-post fails (a transient error), the scheduler
re-attempts it on later passes — as long as the source file still exists — within a bounded time
window, so a blip doesn't silently drop an upload.

## Clip editing & background music

Before upload, clips get a single-pass edit for watchability: **9:16 vertical**, a **burned-in
hook caption** over the first seconds (`PENTAKILL!`, champion name, …) — the biggest retention
lever on the Shorts feed — a small translucent **channel-handle watermark** for the whole clip
(brand recall for viewers who never open the description), a random **royalty-free music track
mixed quietly under the game audio**, and **loudness normalization**. Full games upload as-is.
Fades are **off by default on purpose**: the channel's best-retention Shorts loop seamlessly
(retention leaders sit at 110–144% — viewers rewatching), and a fade telegraphs the loop point.
Controlled by the `editing:` block in `config.yaml` (`hook_caption`, `hook_seconds`,
`watermark_text`, music volume, fade lengths, or `enabled: false` to upload clips raw). If any
edit step fails, the clip falls back to a plain vertical conversion so an upload is never fully
blocked.

## Title A/B testing

Every kill clip is posted with one of three title hook styles, chosen deterministically per clip
and **recorded on the row** so the dashboard can score them against each other:
- **hype** — direct hype statement: `Draven INSANE TRIPLE KILL vs X 🔥`
- **wait** — curiosity gap: `Wait for the TRIPLE KILL 🔥 Draven vs X`
- **why** — lesson framing: `This is why you don't fight Draven 🔥`

The **🧪 Title styles** panel on the metrics Overview compares average views and retention per
style as data accumulates. Run `refresh-drafts` to re-roll queued (unpublished) titles into the
experiment; already-published titles are never touched.

## Optimized for reach & monetization

Titles, descriptions, hashtags, and thumbnails are generated for click-through:
- **Titles** lead with the champion + what happened, add scroll-stopping emoji/hype words
  (`Jarvan IV 1v5 PENTAKILL 😱 | Emerald Jungle #shorts`), and stay under YouTube's 100-char cap.
- **Descriptions** front-load searchable phrasing, then a fixed **SUBSCRIBE call-to-action**
  (subscribers → watch-time → the Partner-Program monetization thresholds).
- **Hashtags** are a tight, high-signal set (`#Shorts` first, champion, streak, then broad reach).
- **Tags** carry long-tail SEO phrases (`<champ> montage`, `league of legends best plays`, …).
- **Composite thumbnails** (full games) get boosted saturation/contrast and a bright accent
  border to pop on the browse shelf.

All of it is still a *draft* — edit anything in the dashboard before you Approve.

Drop your own royalty-free tracks into the `music/` folder (see `music/README.md` for safe
sources like YouTube's Audio Library). An empty folder just skips the music layer — the vertical
conversion, normalization, and fades still apply.

## Composite thumbnails

Full games (and matched clips) get a 1280x720 composite thumbnail: a gameplay frame background,
the champion's official art pulled free from Riot's Data Dragon CDN, and bold impact text
(WIN/LOSS or multi-kill, champion name, KDA, rank/role). Toggle with `thumbnails.composite_enabled`
in `config.yaml`. Two bundled/cached assets support it:
- `assets/fonts/Anton-Regular.ttf` — the impact font (SIL OFL licensed). Committed to the repo.
- `data/ddragon/` — champion art cache, fetched on demand and reused across videos.
If the font or CDN is unavailable, thumbnail generation degrades gracefully to a plain frame grab.

## One-time setup

1. Create a virtual environment and install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[dev]"
   ```
2. Copy `.env.example` to `.env` and fill in:
   - `RIOT_API_KEY` — get one from https://developer.riotgames.com (login with your Riot account).
     **This expires every ~24 hours** and must be refreshed by hand — there's no way to automate
     the portal login. Run `python -m clipfarm.cli check-riot-key` any time to check if it's stale.
   - `RIOT_REGION` — regional routing for match data: `americas` (NA/BR/LAN/LAS/OCE), `europe`
     (EUW/EUNE/TR/RU), or `asia` (KR/JP).
   - `RIOT_PLATFORM` — your platform routing value, e.g. `na1`, `euw1`, `kr`.
   - `RIOT_GAME_NAME` / `RIOT_TAG_LINE` — your Riot ID split on the `#`, e.g. for `Faker#KR1` that's
     `RIOT_GAME_NAME=Faker`, `RIOT_TAG_LINE=KR1`. This just *seeds* the account store on first run;
     after that, switch accounts from the dashboard dropdown or `set-account` (see below) — no need
     to edit `.env` again.
3. Check `config.yaml` — the Ascent folder paths and ffmpeg path already match this machine's
   install, but review the tunable values under `riot:` (especially `clip_roll_tolerance_seconds`
   and `multikill_cluster_seconds` — these are best guesses until real clips exist to tune against).

## Running it

- **Backfill existing recordings** (processes everything already in the Ascent folders):
  ```
  python -m clipfarm.cli backfill
  ```
- **Watch for new recordings + run the dashboard** (does both at once):
  ```
  python -m clipfarm.cli watch
  ```
  Then open http://localhost:8000 to review, edit, and approve items.
- **Check if your Riot API key is still valid**:
  ```
  python -m clipfarm.cli check-riot-key
  ```
- **One-time YouTube authorization** (needed once before any YouTube upload will work -- see
  Phase 2 setup below):
  ```
  python -m clipfarm.cli reauth-youtube
  ```
- **Check if your Facebook Page token is still valid**:
  ```
  python -m clipfarm.cli check-facebook-token
  ```
- **Authorize Facebook analytics** (mints a durable Page token with `read_insights`, the FB
  equivalent of the YouTube analytics scope):
  ```
  python -m clipfarm.cli reauth-facebook
  ```
- **Regenerate draft titles/descriptions/tags for everything not yet published** (run after
  metadata-template changes; already-published items are left untouched):
  ```
  python -m clipfarm.cli refresh-drafts
  ```
- **Back-post YouTube clips to Facebook Reels** (everything already on YouTube but not yet on
  Facebook; idempotent, respects Meta's 30 Reels/24h cap):
  ```
  python -m clipfarm.cli backpost-facebook
  ```
- **Switch accounts** — playing on a smurf/alt? Use the dashboard header dropdown (the `+` adds a
  new one), or the CLI. The Riot ID is validated before switching, and each recording is tagged
  with the account it belongs to, so switching never mis-matches a previous account's clips:
  ```
  python -m clipfarm.cli list-accounts
  python -m clipfarm.cli set-account "Smurfy#EUW" --platform euw1
  ```

## Running tests

```
pytest
```
Tests use fixture JSON and a fake Riot client — no live API calls or credentials required.

## Architecture (where things live)

```
clipfarm/
  watcher/      file watcher -> ingest (waits for the recording to finish writing)
  riot/         Match-V5 client, filename->match matcher, timeline multi-kill analysis,
                metadata_builder (titles/descriptions/tags), champion_names
  media/        ffmpeg wrapper, composite thumbnails (Data Dragon art), edit (vertical+music+fades)
  jobs/         pipeline (ingest->match->metadata->thumbnail), rematch sweep, publish_job,
                scheduler (auto-post), scan (folder re-scan), cleanup (file lifecycle)
  publishers/   registry (the plug-in point), youtube, facebook, dedup (content ledger), quota
  metrics/      base (provider registry), youtube_stats, facebook_stats, poller (hourly
                snapshots), analysis (strong/weak classification + insights + timeline)
  review/       FastAPI app + routes + the dashboard (templates/ + static/)
  paths.py      Ascent filename/folder conventions      autopost.py  scheduler settings store
  accounts.py   multi-account store   riot_key.py  runtime key   fb_settings.py  FB creds
  config.py     config.yaml + .env    db.py  SQLite schema + all queries
```
Runtime state that shouldn't be in code lives in `data/` (SQLite, tokens, caches, account/
credential JSON) — all gitignored. `config.yaml` holds tunables; `.env` holds secrets/seeds.

**Deep dives:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (data flow, DB schema, background
workers, invariants) and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (dev setup, testing,
step-by-step recipes for extending the app).

## Extending: add a publishing platform

Everything routes through `clipfarm/publishers/registry.py`, so a new platform is two steps:

1. Write `clipfarm/publishers/<name>.py` with `publish(media_file) -> str` (return the platform's
   video id; raise with a human-readable message on failure — see `youtube.py` for the pattern,
   including friendly error mapping and success-only quota counting).
2. Add one `Platform(...)` entry to `_REGISTRY` — its name, which media kinds it takes
   (`"clip"`/`"full_game"`), and an `is_enabled` check.

That's it: new items get its checkbox, Approve publishes to it, and the per-platform status
chips + Retry button work automatically. Enabling it later backfills targets onto existing items.

## Roadmap / manual setup per platform

**Phase 2 (YouTube uploads):**
- Create a Google Cloud project at https://console.cloud.google.com, enable "YouTube Data API v3".
- Configure the OAuth consent screen (External, add yourself as a test user), then click "Publish
  App" to move it to "In production" — for a personal project with the `youtube.upload` scope this
  does not trigger Google review, and avoids refresh tokens expiring every 7 days.
- Create an OAuth Client ID of type "Desktop app", download the JSON, point
  `GOOGLE_CLIENT_SECRETS_PATH` in `.env` at it.
- Confirm your channel has completed YouTube's one-time phone verification for uploads over 15
  minutes (full games are typically 30-60+ min).

**Phase 3 (Facebook Page uploads):** enter credentials in the dashboard's **Facebook** panel
(Page ID + Page Access Token + Enabled) — validated live, no `.env` edit or restart. To get a
Page Access Token *without* tripping Meta's Business Verification:
1. Have a Facebook Page you administer, and any Meta app in Development Mode
   (https://developers.facebook.com/apps). If the app wizard's "Manage everything on your Page"
   use case forces business verification, skip that use case — you don't need it.
2. Open the **Graph API Explorer** (https://developers.facebook.com/tools/explorer), select your
   app, and add **only** these permissions: `pages_show_list`, `pages_read_engagement`,
   `pages_manage_posts`. Do **not** add `business_management` (that's what triggers verification).
3. Click **Generate Access Token** and grant your Page. Then query `me/accounts` — copy your
   Page's `access_token` (the Page token) and its `id` (the Page ID).
4. Paste both into the dashboard Facebook panel, check **Enabled**, Save. Posting to your own
   Page in Development Mode needs no App Review.
Once enabled, full games post as Page videos and clips post as Reels.

**Phase 4 (TikTok uploads):**
- Register an app at https://developers.tiktok.com and apply for the `video.publish` scope.
- Complete TikTok Login Kit's OAuth flow once to cache a token.
- Until you submit your app for TikTok's audit (2-4 weeks, may need feedback rounds), all posts
  are forced private (`SELF_ONLY`) — this is a TikTok platform restriction, not a bug. Flip
  `tiktok.force_self_only` to `false` in `config.yaml` once your app is approved for public posting.
