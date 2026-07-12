"""SQLite persistence: which files we've seen, their draft metadata, and per-platform publish status."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from clipfarm.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,                 -- 'full_game' | 'clip'
    recorded_at TEXT NOT NULL,          -- ISO timestamp parsed from filename
    status TEXT NOT NULL DEFAULT 'processing',  -- processing | ready | needs_attention | approved | failed
    riot_match_id TEXT,
    participant_id INTEGER,
    champion TEXT,
    kda TEXT,
    win INTEGER,
    queue_type TEXT,
    game_start_ms INTEGER,
    game_end_ms INTEGER,
    kill_streak INTEGER,                -- for clips: size of detected multi-kill (1 = single kill)
    account TEXT,                       -- Riot ID (Name#TAG) this recording belongs to
    role TEXT,                          -- Top/Jungle/Mid/ADC/Support
    patch TEXT,                         -- e.g. "26.13"
    opponent_champion TEXT,             -- enemy laner in the same position
    rank TEXT,                          -- e.g. "Emerald II" at time of processing
    draft_title TEXT,
    draft_description TEXT,
    draft_hashtags TEXT,                -- JSON list
    draft_tags TEXT,                    -- JSON list (YouTube tags field)
    thumbnail_path TEXT,
    upload_media_path TEXT,             -- path actually uploaded (may differ from source, e.g. 9:16 re-encode)
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS publish_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_file_id INTEGER NOT NULL REFERENCES media_files(id),
    platform TEXT NOT NULL,             -- youtube | facebook | tiktok
    selected INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | uploading | published | failed
    platform_video_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(media_file_id, platform)
);

CREATE TABLE IF NOT EXISTS quota_usage (
    platform TEXT NOT NULL,
    day TEXT NOT NULL,                  -- YYYY-MM-DD
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (platform, day)
);

-- Permanent ledger of what content has already been published to each platform. This is the
-- hard guarantee against double-posting: even a re-ingested/renamed file, a second recording of
-- the same match, or a wiped-and-rebuilt media_files table can't post the same content twice,
-- because publishing checks (and writes) this ledger by a content *signature*, not a row id.
CREATE TABLE IF NOT EXISTS published_content (
    platform TEXT NOT NULL,
    content_key TEXT NOT NULL,          -- e.g. 'match:NA1_123', 'clip:NA1_123:840000', 'file:<hash>'
    media_file_id INTEGER,              -- the row that first published it (informational)
    platform_video_id TEXT,
    published_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, content_key)
);

-- One row per clip the auto-post scheduler fires, so it can enforce a daily cap, per-game cap,
-- and min-gap spacing independent of whether the async upload ultimately succeeds.
CREATE TABLE IF NOT EXISTS autopost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_file_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'clip',   -- 'clip' | 'full_game': separate cadence tracks
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Time-series performance snapshots per published video. Keyed by (platform, platform_video_id)
-- so stats SURVIVE the source recording being deleted (Ascent's 50GB rollover) -- this is the
-- data we keep for analysis even after media_files is archived. Common metrics get columns;
-- provider-specific extras go in extra_json so new metrics need no migration.
CREATE TABLE IF NOT EXISTS video_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    platform_video_id TEXT NOT NULL,
    media_file_id INTEGER,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    watch_time_minutes REAL,
    avg_view_seconds REAL,
    avg_view_pct REAL,
    impressions INTEGER,
    ctr REAL,
    extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_stats_video ON video_stats (platform, platform_video_id, fetched_at);
"""


def _connect() -> sqlite3.Connection:
    db_path: Path = get_settings().database.path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first release; applied idempotently on startup since
# CREATE TABLE IF NOT EXISTS won't alter an existing table.
_MIGRATION_COLUMNS = [
    ("media_files", "role", "TEXT"),
    ("media_files", "patch", "TEXT"),
    ("media_files", "opponent_champion", "TEXT"),
    ("media_files", "rank", "TEXT"),
    ("media_files", "draft_tags", "TEXT"),
    ("media_files", "account", "TEXT"),
    ("media_files", "content_hash", "TEXT"),   # file-content signature for dedup
    ("media_files", "highlight_ms", "INTEGER"),  # game-relative ts of a clip's key kill, for dedup
    ("media_files", "source_deleted_at", "TEXT"),  # set when the on-disk recording is gone (Ascent rollover)
    ("media_files", "published_at", "TEXT"),        # first successful publish time (best-time analysis)
    ("media_files", "title_variant", "TEXT"),       # which A/B title style the draft used (CTR experiment)
    ("media_files", "claim_checked_account", "TEXT"),  # last account we tried to claim this row for (see claim sweep)
    ("autopost_log", "kind", "TEXT DEFAULT 'clip'"),  # separate clip vs full-game cadence tracks
]


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        existing = {
            (table, row[1])
            for table in {t for t, _, _ in _MIGRATION_COLUMNS}
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for table, column, col_type in _MIGRATION_COLUMNS:
            if (table, column) not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_media_file_by_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media_files WHERE path = ?", (path,)).fetchone()


def get_media_file(conn: sqlite3.Connection, media_file_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media_files WHERE id = ?", (media_file_id,)).fetchone()


def insert_media_file(conn: sqlite3.Connection, path: str, kind: str, recorded_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO media_files (path, kind, recorded_at) VALUES (?, ?, ?)",
        (path, kind, recorded_at),
    )
    return cur.lastrowid


def update_media_file(conn: sqlite3.Connection, media_file_id: int, **fields: Any) -> None:
    if not fields:
        return
    if "hashtags" in fields:
        fields["draft_hashtags"] = json.dumps(fields.pop("hashtags"))
    if "tags" in fields:
        fields["draft_tags"] = json.dumps(fields.pop("tags"))
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    set_clause += ", updated_at = datetime('now')"
    conn.execute(f"UPDATE media_files SET {set_clause} WHERE id = ?", (*fields.values(), media_file_id))


def list_queue(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    # Archived rows (source recording deleted by Ascent's rollover) stay in the DB for stats
    # but leave the review panel so it doesn't fill with items nothing can be done to.
    return conn.execute(
        "SELECT * FROM media_files WHERE source_deleted_at IS NULL ORDER BY recorded_at DESC"
    ).fetchall()


def list_matched_full_games(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM media_files WHERE kind = 'full_game' AND riot_match_id IS NOT NULL"
    ).fetchall()


def claimable_media_files(conn: sqlite3.Connection, current_account: str) -> list[sqlite3.Row]:
    """Rows that may actually belong to the now-current account but were processed under a
    different one (mis-tagged), or that previously gave up matching -- candidates for the claim
    sweep that runs on an account switch. A row qualifies when it has no Riot match yet, isn't
    already going out the door, and is either tagged to a DIFFERENT account or in terminal
    'unmatched'. Rows we've already tried to claim for this exact account are excluded (the
    claim marker), so repeated switches don't re-query the Riot API for the same misses. Full
    games first, so a freshly-claimed full game becomes a candidate its clips can match against
    for free on the same pass."""
    return conn.execute(
        """
        SELECT id, kind FROM media_files
        WHERE riot_match_id IS NULL
          AND status NOT IN ('published', 'uploading', 'approved', 'failed')
          AND ((account IS NOT NULL AND account != ?) OR status = 'unmatched')
          AND (claim_checked_account IS NULL OR claim_checked_account != ?)
          AND id NOT IN (
              SELECT media_file_id FROM publish_targets WHERE status IN ('published', 'uploading')
          )
        ORDER BY CASE kind WHEN 'full_game' THEN 0 ELSE 1 END, recorded_at
        """,
        (current_account, current_account),
    ).fetchall()


def clip_metadata_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Matched clips eligible for the metadata-consistency repair: not published/approved (don't
    disturb live or user-edited items) and their source still present. Returns id, rank, and the
    draft title so the caller can spot a title whose rank no longer matches the stored rank."""
    return conn.execute(
        """
        SELECT id, rank, draft_title FROM media_files
        WHERE kind = 'clip' AND riot_match_id IS NOT NULL AND draft_title IS NOT NULL
          AND status NOT IN ('published', 'approved')
          AND source_deleted_at IS NULL
        """
    ).fetchall()


def mark_claim_checked(conn: sqlite3.Connection, media_file_id: int, account: str) -> None:
    """Record that we tried to claim this row for `account` and it didn't match, so the claim
    sweep won't re-hit the Riot API for the same miss on every scan. A real match clears this
    implicitly -- the row then has a riot_match_id and is no longer claimable."""
    conn.execute(
        "UPDATE media_files SET claim_checked_account = ?, updated_at = datetime('now') WHERE id = ?",
        (account, media_file_id),
    )


def upsert_publish_target(conn: sqlite3.Connection, media_file_id: int, platform: str, selected: bool) -> None:
    conn.execute(
        """
        INSERT INTO publish_targets (media_file_id, platform, selected)
        VALUES (?, ?, ?)
        ON CONFLICT(media_file_id, platform) DO UPDATE SET selected = excluded.selected, updated_at = datetime('now')
        """,
        (media_file_id, platform, int(selected)),
    )


def ensure_publish_target(conn: sqlite3.Connection, media_file_id: int, platform: str, selected: bool = True) -> None:
    """Add a target only if one doesn't already exist (preserves the user's prior selection).
    Used to backfill a newly-enabled platform onto existing queue items."""
    conn.execute(
        "INSERT OR IGNORE INTO publish_targets (media_file_id, platform, selected) VALUES (?, ?, ?)",
        (media_file_id, platform, int(selected)),
    )


def get_publish_targets(conn: sqlite3.Connection, media_file_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM publish_targets WHERE media_file_id = ?", (media_file_id,)
    ).fetchall()


def set_publish_target_status(
    conn: sqlite3.Connection,
    media_file_id: int,
    platform: str,
    status: str,
    platform_video_id: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE publish_targets
        SET status = ?, platform_video_id = ?, error_message = ?, updated_at = datetime('now')
        WHERE media_file_id = ? AND platform = ?
        """,
        (status, platform_video_id, error_message, media_file_id, platform),
    )


def find_published_content(
    conn: sqlite3.Connection, platform: str, keys: list[str]
) -> sqlite3.Row | None:
    """Return the ledger row if any of `keys` was already published to `platform`, else None.
    A match means "this content already went out" -- the caller must not publish again."""
    keys = [k for k in keys if k]
    if not keys:
        return None
    placeholders = ", ".join("?" for _ in keys)
    return conn.execute(
        f"SELECT * FROM published_content WHERE platform = ? AND content_key IN ({placeholders}) LIMIT 1",
        (platform, *keys),
    ).fetchone()


def record_published_content(
    conn: sqlite3.Connection,
    platform: str,
    keys: list[str],
    media_file_id: int,
    platform_video_id: str | None,
) -> None:
    """Record every content key for a successful publish. INSERT OR IGNORE so a key already
    claimed by a prior publish is left pointing at the original video."""
    for key in {k for k in keys if k}:
        conn.execute(
            """
            INSERT OR IGNORE INTO published_content (platform, content_key, media_file_id, platform_video_id)
            VALUES (?, ?, ?, ?)
            """,
            (platform, key, media_file_id, platform_video_id),
        )


def try_reserve_content(
    conn: sqlite3.Connection, platform: str, keys: list[str], media_file_id: int
) -> bool:
    """Atomically claim every content key (as a not-yet-finalized reservation) before uploading.

    Because the ledger's PRIMARY KEY (platform, content_key) is enforced by SQLite, exactly one
    of two racing publishers can insert a given key -- the other sees rowcount 0 and loses. This
    closes the window where two different files with identical content, approved simultaneously,
    could both pass a plain "already published?" read and both upload.

    Returns True if we now own all keys (caller must finalize on success / release on failure).
    Returns False if any key was already taken (a duplicate); any keys we grabbed first are
    rolled back so we don't leave a partial reservation behind.
    """
    keys = [k for k in keys if k]
    if not keys:
        return True  # nothing to dedup on; fall through to a normal upload
    inserted: list[str] = []
    for key in keys:
        cur = conn.execute(
            "INSERT OR IGNORE INTO published_content (platform, content_key, media_file_id) VALUES (?, ?, ?)",
            (platform, key, media_file_id),
        )
        if cur.rowcount == 1:
            inserted.append(key)
        else:
            for k in inserted:  # someone else owns a later key -> release the ones we just took
                conn.execute(
                    "DELETE FROM published_content WHERE platform = ? AND content_key = ?", (platform, k)
                )
            return False
    return True


def finalize_reserved_content(
    conn: sqlite3.Connection, platform: str, keys: list[str], media_file_id: int, platform_video_id: str | None
) -> None:
    """Stamp the winning video id onto reservations after a successful upload."""
    for key in [k for k in keys if k]:
        conn.execute(
            "UPDATE published_content SET platform_video_id = ?, media_file_id = ? "
            "WHERE platform = ? AND content_key = ?",
            (platform_video_id, media_file_id, platform, key),
        )


def release_reserved_content(conn: sqlite3.Connection, platform: str, keys: list[str]) -> None:
    """Drop our reservations after a failed upload so the item can be retried later. Only
    unfinalized reservations (no video id yet) are removed -- a real prior publish is untouched."""
    for key in [k for k in keys if k]:
        conn.execute(
            "DELETE FROM published_content WHERE platform = ? AND content_key = ? AND platform_video_id IS NULL",
            (platform, key),
        )


def platforms_already_published(conn: sqlite3.Connection, keys: list[str]) -> list[str]:
    """Which platforms have already published any of these content keys (for a dashboard warning)."""
    keys = [k for k in keys if k]
    if not keys:
        return []
    placeholders = ", ".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT DISTINCT platform FROM published_content WHERE content_key IN ({placeholders})",
        tuple(keys),
    ).fetchall()
    return [r["platform"] for r in rows]


def record_autopost(conn: sqlite3.Connection, media_file_id: int, platform: str, kind: str = "clip") -> None:
    conn.execute(
        "INSERT INTO autopost_log (media_file_id, platform, kind) VALUES (?, ?, ?)",
        (media_file_id, platform, kind),
    )


def autopost_count_today(conn: sqlite3.Connection, platform: str, day: str, kind: str = "clip") -> int:
    """How many items of this track auto-post fired for the platform on `day` (local date)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM autopost_log "
        "WHERE platform = ? AND kind = ? AND date(created_at, 'localtime') = ?",
        (platform, kind, day),
    ).fetchone()
    return row["n"] if row else 0


def autopost_count_for_match(
    conn: sqlite3.Connection, platform: str, match_id: str, kind: str = "clip"
) -> int:
    """How many items from one match this track has already fired (the per-game cap)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM autopost_log a
        JOIN media_files m ON m.id = a.media_file_id
        WHERE a.platform = ? AND a.kind = ? AND m.riot_match_id = ?
        """,
        (platform, kind, match_id),
    ).fetchone()
    return row["n"] if row else 0


def autopost_last_fired(conn: sqlite3.Connection, platform: str, kind: str = "clip") -> str | None:
    """ISO timestamp of the most recent auto-post on this track for spacing, or None."""
    row = conn.execute(
        "SELECT MAX(created_at) AS t FROM autopost_log WHERE platform = ? AND kind = ?",
        (platform, kind),
    ).fetchone()
    return row["t"] if row and row["t"] else None


def failed_autopost_targets(
    conn: sqlite3.Connection, older_than_min: int, younger_than_hours: int
) -> list[sqlite3.Row]:
    """Auto-posted targets that failed and are worth backfilling: source still on disk, last try
    between `older_than_min` ago (don't hammer) and `younger_than_hours` ago (give up eventually)."""
    return conn.execute(
        """
        SELECT DISTINCT pt.media_file_id, pt.platform
        FROM publish_targets pt
        JOIN autopost_log a ON a.media_file_id = pt.media_file_id AND a.platform = pt.platform
        JOIN media_files m ON m.id = pt.media_file_id
        WHERE pt.status = 'failed'
          AND m.source_deleted_at IS NULL
          AND pt.updated_at <= datetime('now', ?)
          AND pt.updated_at >= datetime('now', ?)
        """,
        (f"-{older_than_min} minutes", f"-{younger_than_hours} hours"),
    ).fetchall()


# --- file lifecycle -------------------------------------------------------------------------

def rows_with_live_source(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rows whose source recording we still think is on disk (not yet archived)."""
    return conn.execute(
        "SELECT id, path FROM media_files WHERE source_deleted_at IS NULL"
    ).fetchall()


def mark_source_deleted(conn: sqlite3.Connection, media_file_id: int) -> None:
    conn.execute(
        "UPDATE media_files SET source_deleted_at = datetime('now'), updated_at = datetime('now') "
        "WHERE id = ? AND source_deleted_at IS NULL",
        (media_file_id,),
    )


# --- video performance stats ----------------------------------------------------------------

def published_videos(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every successfully-published video across platforms, with its media_file context. Used by
    the stats poller and the metrics dashboard. Survives source deletion (joins archived rows too)."""
    return conn.execute(
        """
        SELECT pt.platform, pt.platform_video_id, pt.media_file_id,
               m.kind, m.champion, m.role, m.rank, m.kill_streak, m.queue_type,
               m.draft_title, m.recorded_at, m.published_at, m.source_deleted_at
        FROM publish_targets pt
        JOIN media_files m ON m.id = pt.media_file_id
        WHERE pt.status = 'published' AND pt.platform_video_id IS NOT NULL
        """
    ).fetchall()


def insert_video_stats(conn: sqlite3.Connection, platform: str, platform_video_id: str,
                       media_file_id: int | None, metrics: dict) -> None:
    """Record one time-series snapshot. Unknown metrics land in extra_json (no migration needed)."""
    cols = ["views", "likes", "comments", "shares", "watch_time_minutes",
            "avg_view_seconds", "avg_view_pct", "impressions", "ctr"]
    known = {c: metrics.get(c) for c in cols}
    extra = {k: v for k, v in metrics.items() if k not in cols}
    conn.execute(
        f"""
        INSERT INTO video_stats
            (platform, platform_video_id, media_file_id, {', '.join(cols)}, extra_json)
        VALUES (?, ?, ?, {', '.join('?' for _ in cols)}, ?)
        """,
        (platform, platform_video_id, media_file_id, *[known[c] for c in cols],
         json.dumps(extra) if extra else None),
    )


def latest_video_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The most recent snapshot per video, joined to its media_file context, best performers first."""
    return conn.execute(
        """
        SELECT vs.*, m.kind, m.champion, m.role, m.rank, m.kill_streak, m.queue_type,
               m.draft_title, m.recorded_at, m.published_at, m.title_variant
        FROM video_stats vs
        JOIN (
            -- MAX(id), not MAX(fetched_at): id is monotonic, so it breaks same-second ties.
            SELECT MAX(id) AS max_id FROM video_stats GROUP BY platform, platform_video_id
        ) newest ON vs.id = newest.max_id
        LEFT JOIN media_files m ON m.id = vs.media_file_id
        ORDER BY COALESCE(vs.views, 0) DESC
        """
    ).fetchall()


def increment_quota(conn: sqlite3.Connection, platform: str, day: str) -> int:
    conn.execute(
        """
        INSERT INTO quota_usage (platform, day, count) VALUES (?, ?, 1)
        ON CONFLICT(platform, day) DO UPDATE SET count = count + 1
        """,
        (platform, day),
    )
    row = conn.execute(
        "SELECT count FROM quota_usage WHERE platform = ? AND day = ?", (platform, day)
    ).fetchone()
    return row["count"] if row else 0


def get_quota(conn: sqlite3.Connection, platform: str, day: str) -> int:
    row = conn.execute(
        "SELECT count FROM quota_usage WHERE platform = ? AND day = ?", (platform, day)
    ).fetchone()
    return row["count"] if row else 0
