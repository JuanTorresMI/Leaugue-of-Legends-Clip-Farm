# Changelog

All notable changes to LeagueClipFarm. Newest first. Dates are local (America).

The project is pre-1.0 and single-user; this log records behaviour changes, fixes, and any
one-time repairs applied to the live database so they aren't a mystery later.

---

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
