"""Glue: new file on disk -> classify -> Riot match -> metadata -> thumbnail -> review queue row."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from clipfarm import db
from clipfarm.config import get_settings
from clipfarm.jobs.models import DraftMetadata, MatchContext
from clipfarm.media.composite import generate_composite, spec_for_clip, spec_for_full_game
from clipfarm.media.ffmpeg import grab_frame
from clipfarm.media.thumbnail import add_overlay_to_image, generate_thumbnail
from clipfarm.paths import MediaKind, classify_media
from clipfarm.publishers import dedup
from clipfarm.riot.client import RiotKeyExpiredError, get_riot_client
from clipfarm.riot.match_matcher import (
    find_match_containing_timestamp,
    find_match_covering_time,
    find_match_for_full_game,
)
from clipfarm.riot.metadata_builder import build_clip_metadata, build_full_game_metadata, streak_label
from clipfarm.riot.timeline_analysis import analyze_clip_kills

logger = logging.getLogger(__name__)


def _enabled_platforms(kind: MediaKind) -> list[str]:
    """Which platforms a new item routes to. Defined entirely by the publisher registry --
    see clipfarm/publishers/registry.py for how to plug in a new platform."""
    from clipfarm.publishers import registry

    return registry.enabled_platforms(kind.value)


def _row_to_match_context(row: sqlite3.Row) -> MatchContext:
    kills, deaths, assists = (int(x) for x in row["kda"].split("/"))
    return MatchContext(
        match_id=row["riot_match_id"],
        participant_id=row["participant_id"],
        champion=row["champion"],
        kills=kills,
        deaths=deaths,
        assists=assists,
        win=bool(row["win"]),
        queue_type=row["queue_type"],
        game_start_ms=row["game_start_ms"],
        game_end_ms=row["game_end_ms"],
        role=row["role"],
        patch=row["patch"],
        opponent_champion=row["opponent_champion"],
    )


# Match-derived columns that a re-match must clear before re-deriving, so a row that no
# longer matches doesn't keep stale champion/KDA from a previous attempt.
_MATCH_FIELDS = (
    "riot_match_id", "participant_id", "champion", "kda", "win", "queue_type",
    "game_start_ms", "game_end_ms", "role", "patch", "opponent_champion", "kill_streak",
    "highlight_ms", "title_variant", "error_message",
)


def _derive_fields(path: Path, parsed, client) -> dict:
    """Run matching + metadata + thumbnail generation for one recording, returning the DB
    field updates. Never raises -- failures land in the returned status/error_message."""
    settings = get_settings()
    fields: dict = {}

    # Content signature for double-post prevention -- computed once here, then read from the
    # row (no re-hashing) at publish time. See publishers/dedup.py.
    content_hash = dedup.file_content_hash(path)
    if content_hash:
        fields["content_hash"] = content_hash

    try:
        fields["account"] = client.riot_id
        rank = client.get_rank()
        if rank:
            fields["rank"] = rank

        if parsed.kind == MediaKind.FULL_GAME:
            match = find_match_for_full_game(
                client,
                parsed.recorded_at,
                settings.riot.full_game_match_tolerance_minutes,
                settings.riot.full_game_max_duration_minutes,
            )
            if match:
                metadata = build_full_game_metadata(match, parsed.recorded_at, rank)
                fields.update(
                    riot_match_id=match.match_id,
                    participant_id=match.participant_id,
                    champion=match.champion,
                    kda=match.kda,
                    win=int(match.win),
                    queue_type=match.queue_type,
                    game_start_ms=match.game_start_ms,
                    game_end_ms=match.game_end_ms,
                    role=match.role,
                    patch=match.patch,
                    opponent_champion=match.opponent_champion,
                )
                overlay_text = "WIN" if match.win else "LOSS"
            else:
                metadata = DraftMetadata(
                    title=f"League of Legends - Full Game ({parsed.recorded_at:%b %d, %H:%M})",
                    description="Could not confidently match this recording to a specific Riot match.",
                    hashtags=["#LeagueOfLegends", "#LoL"],
                )
                overlay_text = None

            output_jpg = settings.project_root / "data" / "thumbnails" / f"{path.stem}_full_game.jpg"
            if match and settings.thumbnails.composite_enabled:
                # Composite: gameplay frame background + official champion art + bold text.
                frame = settings.project_root / "data" / "thumbnails" / f"{path.stem}_frame.png"
                grab_frame(path, frame, settings.thumbnails.full_game_frame_offset_seconds)
                generate_composite(spec_for_full_game(match, rank), frame, output_jpg)
                frame.unlink(missing_ok=True)
                fields["thumbnail_path"] = str(output_jpg)
            elif parsed.thumbnail_path is not None:
                fields["thumbnail_path"] = str(parsed.thumbnail_path)
            else:
                generate_thumbnail(
                    path, output_jpg, settings.thumbnails.full_game_frame_offset_seconds, overlay_text
                )
                fields["thumbnail_path"] = str(output_jpg)

        else:  # CLIP
            with db.get_conn() as conn:
                candidate_rows = db.list_matched_full_games(conn)
            # match_id -> (account, rank) of the full game that owns it, so a matched clip is
            # attributed to whoever actually PLAYED its game -- account AND rank together --
            # rather than whichever account is active now. This is what lets an account switch
            # correctly claim a mis-tagged clip, and keeps its rank from showing the active
            # account's tier by mistake.
            meta_by_match = {r["riot_match_id"]: (r["account"], r["rank"]) for r in candidate_rows}
            candidates = [_row_to_match_context(row) for row in candidate_rows]
            matched_game = find_match_containing_timestamp(
                candidates, parsed.recorded_at, settings.riot.clip_roll_tolerance_seconds
            )
            if matched_game is None:
                # No processed full game covers this moment (clip may predate its full-game
                # row, or the game wasn't recorded) -- query Match-V5 directly by time. A direct
                # hit came from the current account's own history, so it owns the clip.
                matched_game = find_match_covering_time(
                    client, parsed.recorded_at, settings.riot.full_game_max_duration_minutes
                )
                if matched_game is not None:
                    meta_by_match[matched_game.match_id] = (client.riot_id, rank)

            highlight = None
            effective_rank = rank  # rank of the account that OWNS the matched game (set below)
            if matched_game:
                highlight = analyze_clip_kills(
                    client,
                    matched_game,
                    parsed.recorded_at,
                    settings.riot.clip_roll_tolerance_seconds,
                    settings.riot.multikill_cluster_seconds,
                )
                fields.update(
                    riot_match_id=matched_game.match_id,
                    queue_type=matched_game.queue_type,
                    champion=highlight.champion if highlight else matched_game.champion,
                    kill_streak=highlight.kill_streak if highlight else None,
                    highlight_ms=highlight.first_kill_ms if highlight else None,
                    role=matched_game.role,
                    patch=matched_game.patch,
                )
                owner_account, owner_rank = meta_by_match.get(matched_game.match_id, (None, None))
                if owner_account:
                    # Attribute account AND rank to the game's true owner in lockstep, so a clip
                    # never wears the active account's rank (e.g. a Platinum game tagged Emerald).
                    fields["account"] = owner_account
                    fields["rank"] = owner_rank
                    effective_rank = owner_rank

            metadata = build_clip_metadata(matched_game, highlight, parsed.recorded_at, effective_rank)

            overlay_text = streak_label(highlight.kill_streak).upper() if highlight else None
            output_jpg = settings.project_root / "data" / "thumbnails" / f"{path.stem}_clip.jpg"
            if matched_game and settings.thumbnails.composite_enabled:
                frame = settings.project_root / "data" / "thumbnails" / f"{path.stem}_frame.png"
                grab_frame(path, frame, settings.thumbnails.clip_frame_offset_seconds)
                generate_composite(spec_for_clip(matched_game, highlight, effective_rank), frame, output_jpg)
                frame.unlink(missing_ok=True)
            elif parsed.thumbnail_path is not None:
                # Ascent already picked a representative frame for the clip -- overlay on top of
                # that instead of a fresh (likely worse) ffmpeg frame-grab.
                add_overlay_to_image(parsed.thumbnail_path, output_jpg, overlay_text)
            else:
                generate_thumbnail(path, output_jpg, settings.thumbnails.clip_frame_offset_seconds, overlay_text)
            fields["thumbnail_path"] = str(output_jpg)

        fields["draft_title"] = metadata.title
        fields["draft_description"] = metadata.description
        fields["hashtags"] = metadata.hashtags
        fields["tags"] = metadata.tags
        fields["title_variant"] = metadata.title_variant

        if fields.get("riot_match_id"):
            fields["status"] = "ready"
        else:
            # Unmatched. If the recording is fresh, the game is likely still in progress or
            # not yet indexed by Match-V5 -- park it as awaiting_match for the retry sweep.
            # Old files give up (terminal 'unmatched') so they stop consuming API calls, but
            # still keep generic metadata and can be uploaded.
            age_hours = (datetime.now() - parsed.recorded_at).total_seconds() / 3600
            if age_hours < settings.riot.rematch_give_up_hours:
                fields["status"] = "awaiting_match"
                fields["error_message"] = (
                    "No finished Riot match covers this yet -- will retry automatically."
                )
            else:
                fields["status"] = "unmatched"

    except RiotKeyExpiredError as exc:
        logger.warning("Riot key expired while processing %s", path)
        fields["status"] = "needs_attention"
        fields["error_message"] = str(exc)
    except Exception as exc:  # noqa: BLE001 -- must not crash the watcher loop on a bad file
        logger.exception("Failed to process %s", path)
        fields["status"] = "failed"
        fields["error_message"] = str(exc)

    return fields


def process_new_file(path: Path) -> int | None:
    """Idempotent: returns None (and does nothing) if this path was already processed."""
    settings = get_settings()

    with db.get_conn() as conn:
        if db.get_media_file_by_path(conn, str(path)) is not None:
            logger.info("Already processed, skipping: %s", path)
            return None

    parsed = classify_media(path, settings.ascent.full_games_dir, settings.ascent.clips_dir)

    with db.get_conn() as conn:
        media_file_id = db.insert_media_file(conn, str(path), parsed.kind.value, parsed.recorded_at.isoformat())

    fields = _derive_fields(path, parsed, get_riot_client())

    with db.get_conn() as conn:
        db.update_media_file(conn, media_file_id, **fields)
        for platform in _enabled_platforms(parsed.kind):
            db.upsert_publish_target(conn, media_file_id, platform, selected=True)

    return media_file_id


def _load_for_reprocess(media_file_id: int):
    """(path, parsed) for an existing row, or (None, None) if the row/file is gone."""
    settings = get_settings()
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
    if row is None:
        return None, None
    path = Path(row["path"])
    if not path.exists():
        return None, None
    parsed = classify_media(path, settings.ascent.full_games_dir, settings.ascent.clips_dir)
    return path, parsed


def _commit_reprocess(media_file_id: int, fields: dict) -> None:
    """Write derived fields, clearing stale match columns not set by this run so an item that
    no longer matches doesn't retain old champion/KDA."""
    reset = {col: None for col in _MATCH_FIELDS if col not in fields}
    with db.get_conn() as conn:
        db.update_media_file(conn, media_file_id, **reset, **fields)


def reprocess(media_file_id: int) -> bool:
    """Re-run derivation for an existing row *in place* (same id, preserving the user's
    platform selections). Returns True if it ended up matched. Used by the rematch sweep
    and the on-demand Regenerate button so freshly-finished games get picked up."""
    path, parsed = _load_for_reprocess(media_file_id)
    if path is None:
        return False
    fields = _derive_fields(path, parsed, get_riot_client())
    _commit_reprocess(media_file_id, fields)
    return bool(fields.get("riot_match_id"))


def claim_reprocess(media_file_id: int) -> str:
    """Attempt to match a row against the CURRENT account, but persist ONLY when it actually
    matches -- so an account switch can *adopt* clips that belong to the now-current account
    without overwriting/stealing clips that belong to another account on a miss.

    Returns 'matched' (persisted), 'no_match' (clean miss, left untouched -- safe to mark as
    tried), 'error' (Riot/API problem, left untouched -- retry later), or 'gone' (row/file
    missing). See clipfarm/jobs/rematch.claim_for_current_account for the caller."""
    path, parsed = _load_for_reprocess(media_file_id)
    if path is None:
        return "gone"
    fields = _derive_fields(path, parsed, get_riot_client())
    if fields.get("riot_match_id"):
        _commit_reprocess(media_file_id, fields)
        return "matched"
    # No match: distinguish a real miss (safe to remember) from a transient API failure so we
    # don't permanently mark a row that only failed because the key was down or Riot 5xx'd.
    if fields.get("status") in ("failed", "needs_attention"):
        return "error"
    return "no_match"
