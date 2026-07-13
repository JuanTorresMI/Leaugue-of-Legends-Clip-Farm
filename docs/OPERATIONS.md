# Operations & troubleshooting

How to run LeagueClipFarm day to day **without hitting the problems we've already hit**. Every
rule here maps to a real failure that's now fixed in code — this doc is about not re-triggering
the *workflow* side of them. See [CHANGELOG.md](../CHANGELOG.md) for what changed and
[ARCHITECTURE.md](ARCHITECTURE.md) for how it works.

## The five golden rules

1. **Run exactly one `watch`, and restart it after any code change or `git pull`.** The dashboard,
   scheduler, and all background workers run inside one long-lived `python -m clipfarm.cli watch`
   process. It does **not** hot-reload. If you change code (or I do) and don't restart, the old
   behaviour keeps running — nearly every "it's still broken after the fix" moment traced back to a
   stale `watch` process. Two `watch` processes at once means two schedulers/pollers competing (and
   only one can bind port 8000) — kill them all and start a single fresh one. On Windows:
   `Get-Process python | Stop-Process` then relaunch `python -m clipfarm.cli watch`.

2. **Select the account you're about to play *before* you record.** A clip is tagged with
   whichever account is active when it's ingested. Play on the wrong selection and clips get the
   wrong account/rank until they're healed. The app now self-heals this (see "Wrong account"
   below), but selecting first avoids the round-trip entirely.

3. **Keep the Riot API key fresh.** Dev keys expire every ~24h. When it's stale, matching fails,
   clips sit `awaiting_match`/`unmatched`, and repairs that need Riot get skipped. Check with
   `python -m clipfarm.cli check-riot-key`; hot-swap a new key from the dashboard header (no
   restart needed for the key itself).

4. **Enable a platform in the dashboard — that's all.** Auto-post targets every *enabled*
   platform automatically. There is no separate "which platforms does auto-post use" list to keep
   in sync anymore. Enable Facebook → new clips post there too (after a `watch` restart).

5. **Let it drip; don't bulk-post.** The scheduler posts 1–3/day on purpose. The `backpost-*`
   commands exist for deliberate catch-up, not routine use — a burst of near-identical uploads is
   what gets a young channel dampened.

## Health check (run anytime)

```bash
python -m clipfarm.cli check-riot-key        # Riot key still valid?
python -m clipfarm.cli check-facebook-token  # Facebook Page token still valid?
python -m clipfarm.cli repair-metadata       # fix any clip whose rank drifted from its game (no-op if clean)
```

`repair-metadata` is safe and idempotent — it reports "everything is consistent" when there's
nothing to do, and only reprocesses genuinely-inconsistent, not-yet-published clips.

## Troubleshooting

| Symptom | Likely cause | Do this |
|---|---|---|
| Videos aren't posting to Facebook | Facebook not enabled, or `watch` on old code, or Page token expired | Enable Facebook in the dashboard; **restart `watch`**; `check-facebook-token`. Catch up already-posted clips with `backpost-facebook`. |
| Facebook target `failed` with `WinError 32` / "file used by another process" | Old code: concurrent YouTube+Facebook jobs raced on the shared render | Fixed in code (per-clip render lock). **Restart `watch`**; failed items are retried automatically by the backfill sweep. |
| An approved item failed to upload and just sits there | Pre-fix backfill only retried recent *auto-posted* YouTube failures | Fixed in code: every failed selected target retries automatically (any platform, approved or auto-posted), paced 2/tick, up to 8 lifetime attempts. Past 8, use the dashboard **Retry** button (it ignores the cap). |
| Items stuck `failed` with a `429 Too Many Requests` error | Pre-fix code treated a transient Riot rate-limit as permanent | Fixed in code: 429/5xx/network errors now park as `awaiting_match` and the rematch sweep retries them. Old stuck rows: reprocess via the dashboard Regenerate button. |
| A clip shows the wrong rank (e.g. Platinum game says "Emerald") | Clip processed while the wrong account was selected | `python -m clipfarm.cli repair-metadata` (regenerates title + thumbnail). Prevented going forward by the owner-attribution logic. |
| Clips from an account stay `awaiting_match`/generic after switching to it | They were processed under a different account and are stranded | Switch to the owning account (or hit manual scan) with a **valid Riot key** — the claim pass adopts the ones that match. |
| Lots of items stuck `awaiting_match` | Riot key expired, or games genuinely not finished/indexed yet | `check-riot-key`; if valid, they'll match on the next sweep once the games are in Riot's history. |
| A fix "didn't take" | `watch` still running the old code | **Restart `watch`.** (Rule #1.) |
| Facebook full-game upload fails with "(#100) target is required" | Old pre-fix code running | Restart `watch` — the chunked-upload fix POSTs every phase to `/{page-id}/videos`. |
| Metrics/analytics missing (watch time, retention, subs) | Analytics scope/API not authorized | YouTube: `reauth-youtube` + enable the Analytics API in Google Cloud. Facebook: `reauth-facebook` for `read_insights`. |

## Why these problems can't silently recur (the code-level guarantees)

You don't have to remember these — they're enforced — but they explain why the golden rules are
enough:

- **A clip's account/rank/title/thumbnail are always derived together** from the account that owns
  the matched game (a matched full game is reliably owned by whoever played it). No code path
  patches one field in isolation. → no more "Platinum row, Emerald title."
- **Auto-post targets are read from platform enablement**, not a hand-kept list. → enabling a
  platform can't be silently ignored.
- **The claim sweep persists only on a real match** and marks its misses. → switching accounts
  heals stranded clips without stealing another account's, and without re-hammering Riot.
- **The dedup ledger** makes double-posting impossible regardless of retries/backfills.
- **`video_stats` is append-only**, so analytics history survives source deletion and repairs.
- **Failed uploads self-heal**: every failed selected target (any platform, manual or auto)
  is retried by the scheduler — paced, capped at 8 lifetime attempts — so "approved but never
  posted" can't happen silently.
- **Transient Riot errors are never terminal**: a 429/5xx parks the row for the rematch sweep
  instead of failing it, so a burst of games processed at once can't strand recordings.

## Manual maintenance commands (reference)

| Command | When |
|---|---|
| `python -m clipfarm.cli watch` | Normal operation (dashboard + workers). Restart after code changes. |
| Auto-post → Schedule → **Explore** | Run the posting-time experiment: slots rotate through the test window daily. After 2–4 weeks, lock in the winners via the metrics page's Apply-recommended button (returns the schedule to fixed). |
| `check-riot-key` / `check-facebook-token` | Confirm credentials before a session or a repair. |
| `repair-metadata` | After noticing any rank/title drift, or as a periodic sanity pass. |
| `backpost-facebook` | Deliberate one-time catch-up of clips already on YouTube but not Facebook. |
| `refresh-drafts` | After changing metadata/title templates (regenerates unpublished drafts; spreads new A/B title variants). |
| `reauth-youtube` / `reauth-facebook` | Re-grant analytics scopes if metrics go missing. |
| `list-accounts` / `set-account` | Inspect or switch the active Riot account from the CLI. |
