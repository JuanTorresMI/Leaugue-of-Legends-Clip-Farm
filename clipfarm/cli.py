"""python -m clipfarm.cli watch|backfill|check-riot-key"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from clipfarm import db
from clipfarm.config import get_settings


def _configure_logging() -> None:
    settings = get_settings().logging
    settings.path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=settings.level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(settings.path, encoding="utf-8")],
    )
    # Keep the console readable: these libraries are chatty at INFO for routine operations.
    logging.getLogger("riotwatcher").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)


def cmd_watch(_: argparse.Namespace) -> None:
    import uvicorn

    from clipfarm.watcher.file_watcher import start_watching

    db.init_db()
    observer = start_watching()
    print("Dashboard: http://localhost:8000")
    try:
        # access_log off: the dashboard live-polls every few seconds, which would otherwise
        # flood the console with GET lines and bury the logs that matter.
        uvicorn.run("clipfarm.review.app:app", host="127.0.0.1", port=8000, reload=False, access_log=False)
    finally:
        observer.stop()
        observer.join()


def cmd_backfill(_: argparse.Namespace) -> None:
    from scripts.backfill_existing import run_backfill

    db.init_db()
    run_backfill()


def cmd_check_riot_key(_: argparse.Namespace) -> None:
    from clipfarm.riot.client import get_riot_client

    ok = get_riot_client().check_key()
    print("Riot API key: OK" if ok else "Riot API key: EXPIRED/INVALID -- refresh it in .env")
    sys.exit(0 if ok else 1)


def cmd_reauth_youtube(_: argparse.Namespace) -> None:
    from scripts.reauth_youtube import run_reauth

    run_reauth()


def cmd_check_facebook_token(_: argparse.Namespace) -> None:
    from clipfarm.publishers.facebook import check_token

    ok = check_token()
    print("Facebook Page token: OK" if ok else "Facebook Page token: INVALID/MISSING -- check .env")
    sys.exit(0 if ok else 1)


def cmd_reauth_facebook(_: argparse.Namespace) -> None:
    from scripts.reauth_facebook import run_reauth

    run_reauth()


def cmd_list_accounts(_: argparse.Namespace) -> None:
    from clipfarm import accounts

    current = accounts.current_account().riot_id
    for a in accounts.list_accounts():
        marker = "*" if a.riot_id == current else " "
        print(f"{marker} {a.riot_id}  ({a.platform} / {a.region})")


def cmd_set_account(args: argparse.Namespace) -> None:
    from clipfarm import accounts
    from clipfarm.riot.client import AccountNotFoundError, RiotKeyExpiredError, reset_riot_client, validate_account

    if "#" not in args.riot_id:
        print("Riot ID must be Name#TAG, e.g. Faker#KR1")
        sys.exit(1)
    game_name, tag_line = args.riot_id.rsplit("#", 1)
    game_name, tag_line = game_name.strip(), tag_line.strip()

    known = {a.riot_id: a for a in accounts.list_accounts()}
    if args.riot_id in known and not args.platform:
        platform = known[args.riot_id].platform
        region = known[args.riot_id].region
    else:
        platform = args.platform or "na1"
        region = accounts.region_for_platform(platform)

    try:
        validate_account(game_name, tag_line, region)
    except (AccountNotFoundError, RiotKeyExpiredError) as exc:
        print(f"Could not switch: {exc}")
        sys.exit(1)

    accounts.upsert_account(game_name, tag_line, platform, region)
    accounts.set_current(f"{game_name}#{tag_line}")
    reset_riot_client()
    print(f"Active account is now {game_name}#{tag_line} ({platform})")


def cmd_refresh_drafts(_: argparse.Namespace) -> None:
    """Regenerate titles/descriptions/tags/thumbnails for everything not yet published, in
    place (ids stable). Useful after metadata- or thumbnail-template changes. Published and
    in-flight items keep their live metadata."""
    from clipfarm.jobs.pipeline import reprocess

    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, path FROM media_files
            WHERE status != 'published'
              AND id NOT IN (
                SELECT media_file_id FROM publish_targets WHERE status IN ('published', 'uploading')
              )
            ORDER BY CASE kind WHEN 'full_game' THEN 0 ELSE 1 END, recorded_at
            """
        ).fetchall()

    print(f"Regenerating {len(rows)} drafts in place (full games first, then clips)...")
    for row in rows:
        matched = reprocess(row["id"])
        name = Path(row["path"]).name
        print(f"  {name} -> {'matched' if matched else 'unmatched'}")


def cmd_backpost_facebook(_: argparse.Namespace) -> None:
    """Post every clip already published to YouTube (but not yet to Facebook) as a Reel.
    Idempotent -- re-running skips clips already on Facebook. Respects Meta's 30 Reels/24h cap."""
    from clipfarm.publishers import facebook
    from clipfarm.publishers.facebook import FacebookQuotaExceededError

    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, draft_title FROM media_files
            WHERE kind = 'clip'
              AND id IN (SELECT media_file_id FROM publish_targets WHERE platform='youtube' AND status='published')
              AND id NOT IN (SELECT media_file_id FROM publish_targets WHERE platform='facebook' AND status='published')
            ORDER BY id
            """
        ).fetchall()

    print(f"Back-posting {len(rows)} YouTube clips to Facebook Reels...")
    posted = failed = 0
    for row in rows:
        with db.get_conn() as conn:
            media_file = db.get_media_file(conn, row["id"])
            db.ensure_publish_target(conn, row["id"], "facebook", selected=True)
            db.set_publish_target_status(conn, row["id"], "facebook", "uploading")
        try:
            vid = facebook.publish(media_file)
        except FacebookQuotaExceededError as exc:
            print(f"  stopping -- {exc}")
            with db.get_conn() as conn:
                db.set_publish_target_status(conn, row["id"], "facebook", "pending")
            break
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAILED {row['id']} ({row['draft_title']}): {exc}")
            with db.get_conn() as conn:
                db.set_publish_target_status(conn, row["id"], "facebook", "failed", error_message=str(exc))
        else:
            posted += 1
            print(f"  posted {row['id']} -> reel {vid} | {row['draft_title']}")
            with db.get_conn() as conn:
                db.set_publish_target_status(conn, row["id"], "facebook", "published", platform_video_id=vid)
    print(f"Done: {posted} posted, {failed} failed.")


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="clipfarm")
    subparsers = parser.add_subparsers(required=True)

    subparsers.add_parser("watch", help="Start the file watcher and review dashboard").set_defaults(func=cmd_watch)
    subparsers.add_parser("backfill", help="Process existing Ascent recordings").set_defaults(func=cmd_backfill)
    subparsers.add_parser("check-riot-key", help="Check whether the Riot API key is valid").set_defaults(
        func=cmd_check_riot_key
    )
    subparsers.add_parser(
        "reauth-youtube", help="One-time browser OAuth consent to authorize YouTube uploads"
    ).set_defaults(func=cmd_reauth_youtube)
    subparsers.add_parser(
        "check-facebook-token", help="Check whether the Facebook Page access token is valid"
    ).set_defaults(func=cmd_check_facebook_token)
    subparsers.add_parser(
        "reauth-facebook",
        help="Mint a durable Facebook Page token with read_insights (enables video analytics)",
    ).set_defaults(func=cmd_reauth_facebook)
    subparsers.add_parser(
        "refresh-drafts", help="Regenerate draft metadata for all unpublished items"
    ).set_defaults(func=cmd_refresh_drafts)
    subparsers.add_parser(
        "backpost-facebook", help="Post YouTube-published clips to Facebook Reels (idempotent)"
    ).set_defaults(func=cmd_backpost_facebook)
    subparsers.add_parser("list-accounts", help="List saved Riot accounts (* = active)").set_defaults(
        func=cmd_list_accounts
    )
    set_acct = subparsers.add_parser("set-account", help="Switch the active Riot account")
    set_acct.add_argument("riot_id", help="Riot ID as Name#TAG")
    set_acct.add_argument("--platform", help="Server platform (na1, euw1, kr, ...); required for a new account")
    set_acct.set_defaults(func=cmd_set_account)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
