# Changelog

All notable changes to LeagueClipFarm. Newest first. Dates are local (America).

The project is pre-1.0 and single-user; this log records behaviour changes, fixes, and any
one-time repairs applied to the live database so they aren't a mystery later.

---

## 2026-10-03

### Added — a title pool instead of three templates, and no-repeat posting
- Kill-clip titles now come from a pool of ~70 sentence shapes across four hook families
  (`hype`, `wait`, `why`, and the new `question`), with the suffix style, verb, adjective and
  emoji rolled independently, all seeded by match + kill timestamp. Same-champion clips no
  longer read as one template with the victim swapped. Three-victim phrases now name all three.
- Each draft stores one alternate title per family (`media_files.draft_title_alternates`) and
  the clip's victims (`media_files.victim_champions`); both are idempotent column migrations.
- **Auto-post → Vary consecutive posts** (new setting, default on): the scheduler penalises a
  clip of the champion posted last (-55) or the time before (-25) so the feed alternates
  champions where it can, and switches a clip's title to a different family when the previous
  post used the same one (hand-edited titles are never touched). Two near-identical uploads in
  a row were being read as reposts.
- Description "about" and subscribe lines rotate through a few wordings.

### Changed — clip thumbnails are 9:16 cards built around the clip's own frame
- A crisp band of the actual gameplay (grabbed 35-70% through the clip, seeded by filename,
  instead of a fixed 1.5s), champion art on a seeded side with feathered edges, streak or
  champion as the headline, a `vs` row of the victims' Data Dragon icons, and a per-champion
  accent colour. The composite is now also uploaded for Shorts (search / channel grid /
  subscriptions show it; the feed itself still shows the video). The dashboard shows the
  whole card (`object-fit: contain`).
- Regenerate Thumbnail on an existing clip rebuilds it in the new layout; victims appear once
  the row has been reprocessed (`refresh-drafts`) since older rows don't store them yet.

### Changed — titles, descriptions, and hashtags rewritten to read like a person (SEO pass)
- Every title shape dropped the tells of a generated title: the `#shorts` suffix (YouTube
  detects the format from the video; the tag just looks spammy), em dashes, stacked emoji
  (now at most one, only on the direct-hype variant), and SHOUTED hype adjectives (`INSANE`,
  `DISGUSTING`, …). The rank/role phrase (`| Emerald ADC`) is kept for search but dropped
  before the hook would be truncated in the Shorts feed's two lines.
- The three A/B hook styles now differ in sentence *shape*, not just word choice:
  `Draven Triple Kill vs Jinx & Lulu 🔥 | Emerald ADC` /
  `Wait for the triple kill… Draven vs Jinx & Lulu | Emerald ADC` /
  `This is why you don't fight Draven in Emerald (Triple Kill)`. The solo-kill "why" variant is
  the literal matchup query, `How to punish Jinx as Draven`, and `<champ> vs <victim>` is now a
  search tag on every kill clip.
- Descriptions open with one searchable sentence, then a single `Subscribe for more <champ>.`
  line instead of the `👉 SUBSCRIBE for daily … — new clips every day!` banner. Facebook keeps
  swapping it for "Follow" whatever the casing.
- Hashtags are four or five, ordered for the three YouTube shows above the title (champion,
  `#LeagueOfLegends`, streak, `#LoLClips`, then `#Shorts` last); `#Gaming` is gone.
- Full-game titles use `|` instead of an em dash; the loss thumbnail no longer wears a `LOSS`
  badge or the losing KDA (it leads with the `vs <opponent>` matchup, like the title does), and
  the accent border is half as thick.
- The auto-comment pool reads like a creator's comments (lowercase, a real question, no "new
  clips every day!!").
- **To roll these onto queued clips:** `python -m clipfarm.cli refresh-drafts` (published
  items are untouched).

### Changed — clip overlays restyled so edits don't look machine-made
- Every timed text overlay (hook caption, subscribe ask, like ask) now **fades in and out**
  over ~0.25s instead of popping on and off.
- The hook caption is slightly smaller with a soft drop shadow instead of a fat black outline,
  and loses its exclamation mark (`PENTAKILL`, `TRIPLE KILL`).
- The subscribe and like asks are now **lower-third pills** (translucent rounded box, mixed case
  as typed in config) sitting just under the gameplay frame, instead of giant caps in the
  upper third. Default text is `Subscribe for more`. The mid-clip like ask defaults to **off**
  (`like_cta_text: ""`); set it to turn it back on.
- A clip with no kill and no champion gets **no caption** (the `WATCH THIS` fallback is gone).
- The watermark moved slightly lower (`y=77%`) so it never collides with the pills.
- **To re-render queued clips with the new look**, delete `data/converted/` (it is a cache;
  clips re-render on their next publish).

### Fixed — the test suite runs on a fresh clone
- `pytest` needed a filled-in `.env` (two modules loaded settings at import). An autouse fixture
  now supplies dummy secrets, ignores the real `.env`, and sandboxes `PROJECT_ROOT`, so tests
  never touch real credentials or write into `data/`.

## 2026-07-18

### Fixed — the posting-time experiment was learning from poisoned data
- `hour_performance()` (and therefore `recommended_hours()` / Apply-recommended) averaged
  **full games** (~0 views by nature, all stacked on the full-game hour) and **Facebook**
  numbers (5–15 views) into the per-hour view averages. The full-game hour looked
  catastrophic (15:00 showed 110 avg views; the real clips-only number is 932) and every
  hour was diluted. Now scored on **YouTube clips only** — the thing the clip schedule
  actually decides. Corrected leaders so far: 16:00, 23:00, 21:00.

### Added — mid-clip like ask ("LIKE IF THAT WAS CLEAN")
- A brief flash at ~55% of each clip, in the same upper-third slot as the hook and the
  subscribe ask (the three windows never overlap; skipped entirely on clips too short to
  keep them apart). Likes are the one ranking signal a viewer can give without leaving the
  video, our best performers are exactly the ones that collected likes (the penta: 14; the
  11-like double: 1,067 views), and nothing was asking. Verified on a real NVENC render
  (flash present at 19.5s, absent at 10s). Config `editing.like_cta_text` / `like_cta_seconds`.

### Changed — auto-comment: rotation pool + a visible "inactive" warning
- The seeded first comment now rotates deterministically through `youtube.auto_comment_texts`
  (4 question variants) instead of repeating one sentence several times a day — identical
  repeated comments read as spam to viewers and to YouTube's filter.
- The dashboard now shows a header banner when auto-comment is configured but the cached
  token predates the comments permission (new `/api/youtube-health`, token-file check only).
  It has been silently skipped since 07-15 — **run `python -m clipfarm.cli reauth-youtube`
  once to activate it.**

## 2026-07-15

### Added — auto-comment engagement CTA on every YouTube upload
- Every upload now gets the channel's own first comment (config `youtube.auto_comment_text`,
  default asks "Which champ should I clip next? 👇"). A question invites replies, and comment
  activity is one of the few ranking signals we can seed ourselves — Shorts stall in the
  ~1–2k test pool partly on engagement, and our uploads had none. Cosmetic like the thumbnail
  and playlist steps: a comment failure never fails a finished upload.
- Requires the `youtube.force-ssl` scope, now in `SCOPES` — **run `reauth-youtube` once** to
  activate; until then uploads work as before and the comment is skipped with a log hint.
  Credential loading now uses the token file's own scopes so adding a scope to `SCOPES` can
  never break refreshes of an older token.

### Fixed — full-game packaging was repelling the click (all VODs sat at ~0 views)
- Titles like "Kassadin vs Talon Mid - Platinum II Ranked Solo/Duo LOSS 4/8/12 💪" are
  robot-speak, advertise the loss, and bury the searchable phrase. New format leads with the
  matchup, keeps the "Full Gameplay" search phrase, shows the score only when it's a brag
  (win with recorded kills — no "LOSS 4/8/12", no "0/0/0" remakes):
  `13/2/8 Kassadin vs Talon Mid — Platinum Full Gameplay (Patch 26.13)`. Result/KDA/queue
  stay in the description for search.
- **One-time repair:** all 82 unpublished matched full games (ready + approved) were
  retitled in place from their stored match context — no Riot calls, thumbnails unaffected
  (they render from match context, not the title).

### Fixed — champion ranking mixed dead full games into the average
- The metrics page's "what should I play?" champion panel averaged full games (~0 views by
  nature) together with clips, so champions with more VODs published ranked unfairly low.
  Champion performance now scores **clips only**; the kind panel still compares clip vs
  full-game as before.

## 2026-07-13

### Added — posting-time experiment (explore schedule mode)
- The auto-post schedule now has two modes (dashboard → Auto-post → Schedule):
  **Fixed hours** posts at exactly `post_hours` every day (unchanged behaviour, still the
  default). **Explore (test times)** keeps the same number of posts per day but rotates the
  slots through a configurable test window (default 11:00–23:00) on a deterministic daily
  cycle — spread across the day like a normal schedule, different hours tomorrow, full window
  coverage within ~a week. That variation is what feeds the metrics page's hour-performance
  table real samples; a fixed schedule can only ever learn about its own 3 hours.
- The dashboard status line shows today's rotated slots (`🧪 exploring — today: 13:00, 16:00,
  20:00`), and the metrics page's **Apply recommended** button now also flips the schedule
  back to fixed — run the experiment for 2–4 weeks, then one click locks in the winning hours.
- Implementation: `autopost.explore_hours_for` / `effective_post_hours` (pure, date-seeded),
  used by `scheduler.run_once` + `due_status`; settings fields `schedule_mode` /
  `explore_window` flow through the existing `/api/autopost` endpoints.

### Fixed — failed uploads now actually self-heal
- **The retry backfill never worked for Facebook (or any non-YouTube platform).** It joined
  failed targets against `autopost_log` on *platform*, but auto-posts only log their primary
  platform (YouTube) — so a failed Facebook target could never match and sat failed forever.
  It also ignored **manually-approved** items entirely (an Approve click that failed stayed
  failed unless someone noticed and pressed Retry), and gave up on anything older than 48h —
  which stranded every failure that outlived the bug that caused it.
- Replaced with `retry_failed_publishes`: covers every failed *selected* target whose item is
  still meant to go out (approved/ready, source on disk), on any platform. Bounded by a
  per-target lifetime attempt cap (`retry_count` column, default 8 — no infinite churn on a
  permanently-bad item; the dashboard Retry button ignores the cap) and paced to 2 per tick so
  a backlog drains as a drip. The 9 stranded failures from 07-10..07-12 drain automatically
  after a `watch` restart.

### Fixed — a Riot 429 no longer permanently strands a recording
- Transient Riot/API errors (429 rate limit, 5xx, network drops) during processing used to set
  terminal `status='failed'`, which every sweep explicitly skips — 3 full games from 07-10 were
  stranded that way. Transient errors now park the row as `awaiting_match`, which the rematch
  sweep retries; the claim sweep also recognizes them and won't mark the row as a checked miss.
- **One-time repair:** the 3 stranded full games were reprocessed and all matched (Zeri games,
  including a 13/1/6 win), now `ready` for the long-form autopost track.

### Added — subscriber-growth features
- **Subscribe flash:** every clip now ends with a "SUBSCRIBE FOR MORE" overlay in its final
  2.5s (config `editing.subscribe_cta_text` / `subscribe_cta_seconds`, same upper-third slot as
  the hook caption — they never show at once). Shorts viewers act inside the video or not at
  all; nothing was asking them. Verified on a real NVENC render (frame grabs: CTA present at
  the end, absent mid-clip). Pre-CTA render caches were cleared (4.4 GB) so queued clips
  re-render with it.
- **Champion playlists:** full-game uploads are now filed into a public "«Champion» Full Games"
  playlist (created on first use). Playlists chain in Up Next and turn a champion-curious
  searcher into a session. Failure is cosmetic — an upload never fails over playlist trouble.
- **Facebook wording:** descriptions posted to Facebook now say FOLLOW instead of the
  YouTube-speak SUBSCRIBE.

### Fixed — Facebook uploads failing on a render race
- **Clips now render exactly once even when published to several platforms at the same time.**
  After auto-post began targeting YouTube *and* Facebook, both publish jobs (run concurrently in
  the publish thread pool) called `prepared_clip()` for the same clip and collided writing/
  deleting the shared `_edited.mp4` / caption PNG — on Windows the Facebook job died with
  `WinError 32` ("file used by another process"), so nothing reached Facebook. Added a per-clip
  lock in `media/edit.prepared_clip` (double-checked cache) so the render happens once and the
  other platform reuses it. Regression test covers the concurrent case.
- **Operational note:** this fix (and the auto-post-to-Facebook change) only takes effect after
  restarting `watch`; make sure only **one** `watch` process is running (see OPERATIONS.md).

## 2026-07-12

### Fixed — Facebook auto-posting (`501e6a3`)
- **Auto-post now publishes to every *enabled* platform, not just YouTube.** The scheduler had
  published only to its own `settings.platforms` list, which defaulted to `['youtube']` and had
  no dashboard control — so enabling Facebook did nothing for auto-posts and **429 Facebook
  targets piled up as `pending`, never attempted.** Targets are now derived from the publisher
  registry's enabled platforms (`scheduler.enabled_targets`), so turning a platform on
  immediately includes it (clips → Reels, full games → Page videos). YouTube stays the
  bookkeeping anchor for spacing/daily-count. The auto-post API/dashboard now reports the real
  targets.
- Token, publisher code, and enablement were all fine — only the target list was wrong.
- **Catch-up:** clips auto-posted before Facebook was enabled aren't retro-posted by the drip
  scheduler; run `python -m clipfarm.cli backpost-facebook` (18 items were outstanding: 7 clips +
  11 full games).

### Fixed — clip rank/attribution consistency (`af777a5`, `a785203`)
- **Clips no longer wear the wrong account's rank** (e.g. a Platinum game shown as "Emerald"). A
  clip matches a game by timestamp against *all* accounts' games, but the old code stamped it with
  whichever account was *active* at processing time. Now a matched clip's `account`, `rank`, title
  rank, and thumbnail rank are all derived from the **matched game's owner** in lockstep
  (`pipeline._derive_fields` via `meta_by_match`).
- **Removed the shallow on-scan `repair_clip_attribution`.** It patched the `account`/`rank`
  columns but left the title and thumbnail (baked together at processing time) stale, creating
  "Platinum row / Emerald title" inconsistencies on every scan.
- **Added `repair-metadata` CLI + `rematch.repair_stale_metadata`.** Finds clips whose title rank
  disagrees with their stored rank and **reprocesses** them so title + thumbnail + fields are
  regenerated as one consistent set. Skips published/approved clips; needs a valid Riot key;
  re-runnable.
- **One-time live-DB repairs applied:** 26 clips had their `account`/`rank` corrected from their
  matched full game (17 AltOne, 9 AltTwo — all Platinum games mis-tagged Mainacc/Emerald); then
  9 clips were reprocessed to regenerate their stale "Emerald" titles/thumbnails. Post-repair
  health check: 0 title/rank desync, 0 field mismatches, 0 missing thumbnails.

### Fixed — account-switch stranding (`21ee0fc`)
- **Switching accounts now heals recordings processed under the wrong account.** Previously a
  recording tagged to a different account (or one that gave up matching) was skipped forever:
  `process_new_file` skips known paths and the re-match sweep is scoped to the current account.
- Added a **claim pass** (`rematch.claim_for_current_account`, runs on account switch / manual
  scan) that re-attempts stranded recordings and **adopts only the ones that actually match** the
  now-current account (`pipeline.claim_reprocess` persists only on a match — never steals another
  account's clips). A per-row/per-account marker (`claim_checked_account`) stops repeated switches
  from re-querying Riot for the same misses.

### Added — growth features (`2424701`)
- **Title A/B testing:** every kill clip gets one of three hook styles (direct hype / "wait for
  it" / "this is why"), chosen deterministically and recorded in `media_files.title_variant`; the
  metrics dashboard scores views + retention per style. 299 existing clips backfilled as the
  `hype` baseline.
- **Velocity analytics:** per-video "views gained in the last 24h" and lifetime views/day from the
  append-only stats series; a **🚀 Rising now** dashboard panel and a sortable 24h-delta column.
- **Loop-friendly edits:** clip fades now default OFF (best-retention Shorts loop seamlessly; a
  fade telegraphs the loop point). Zeroed fades no longer emit no-op filters.
- **Channel watermark:** a small translucent `@handle` (config `editing.watermark_text`) burned
  on every clip for brand recall.
- Dashboard: Views(24h) stat cards, Rising now + Title styles panels.

### Changed — project upkeep (`53edccc`, `97f7d13`)
- Initialized the **git repository** (hardened `.gitignore`, `.gitattributes`); verified no
  secrets tracked.
- Added **ruff** linting (`[tool.ruff]`, 120 cols) and fixed all findings; package docstrings on
  every `__init__`.
- Wrote **`docs/ARCHITECTURE.md`** and **`docs/DEVELOPMENT.md`**; corrected README references
  (added `backpost-facebook`, completed the architecture tree).
- Rebuilt the **metrics dashboard** into 3 tabs (Overview / YouTube / Facebook) with a
  strong/weak classification engine (`metrics/analysis.py`), growth chart, and best-time-to-post.
- Wired up **full analytics**: YouTube (`reauth-youtube` + Analytics API — watch time, retention,
  subscribers gained; impressions/CTR are not available from the on-demand API) and Facebook
  (`reauth-facebook`, `read_insights`; Reels vs Page videos request separate metric families).

---

## Database migrations added this cycle

Applied idempotently on startup (`db._MIGRATION_COLUMNS`), so no manual step is needed:

| Column | Purpose |
|---|---|
| `media_files.title_variant` | which A/B title hook style the draft used |
| `media_files.claim_checked_account` | last account the claim sweep tried this row for (dedupes Riot calls) |
| `publish_targets.retry_count` | failed attempts so far (caps the auto-retry backfill at 8) |
