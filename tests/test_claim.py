"""Account-switch claim: adopting recordings that belong to the now-current account but were
processed while a different account was active (mis-tagged) or previously gave up matching."""
import itertools
from types import SimpleNamespace

import pytest

from clipfarm import db
from clipfarm.jobs import pipeline

_seq = itertools.count()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "clipfarm.sqlite3"
    settings = SimpleNamespace(database=SimpleNamespace(path=dbfile))
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.init_db()
    return dbfile


def _row(conn, *, account, status, kind="clip", matched=False, published=False, claim_checked=None):
    mid = db.insert_media_file(conn, f"C:/v/{account}-{status}-{kind}-{next(_seq)}.mp4", kind,
                               "2026-07-01T00:00:00")
    fields = {"status": status, "account": account, "claim_checked_account": claim_checked}
    if matched:
        fields["riot_match_id"] = "NA1_123"
    db.update_media_file(conn, mid, **fields)
    if published:
        db.ensure_publish_target(conn, mid, "youtube", selected=True)
        db.set_publish_target_status(conn, mid, "youtube", "published", platform_video_id="v1")
    return mid


def _claimable_ids(current):
    with db.get_conn() as conn:
        return {r["id"] for r in db.claimable_media_files(conn, current)}


def test_mis_tagged_other_account_row_is_claimable(temp_db):
    with db.get_conn() as conn:
        other = _row(conn, account="Mainacc#NA1", status="unmatched")       # different account
        awaiting = _row(conn, account="Mainacc#NA1", status="awaiting_match")
        mine_gaveup = _row(conn, account="AltTwo#NA1", status="unmatched")    # my own give-up
    ids = _claimable_ids("AltTwo#NA1")
    assert other in ids and awaiting in ids and mine_gaveup in ids


def test_current_account_pending_is_not_claimed_here(temp_db):
    # A current-account item still normally awaiting_match is the rematch sweep's job, not the
    # claim sweep's -- claiming only picks up other-account rows or terminal give-ups.
    with db.get_conn() as conn:
        pending = _row(conn, account="AltTwo#NA1", status="awaiting_match")
    assert pending not in _claimable_ids("AltTwo#NA1")


def test_matched_going_out_and_already_tried_are_excluded(temp_db):
    with db.get_conn() as conn:
        matched = _row(conn, account="Mainacc#NA1", status="ready", matched=True)
        published = _row(conn, account="Mainacc#NA1", status="unmatched", published=True)
        failed = _row(conn, account="Mainacc#NA1", status="failed")
        already = _row(conn, account="Mainacc#NA1", status="unmatched", claim_checked="AltTwo#NA1")
    ids = _claimable_ids("AltTwo#NA1")
    assert matched not in ids and published not in ids and failed not in ids and already not in ids


def test_mark_claim_checked_excludes_next_time(temp_db):
    with db.get_conn() as conn:
        mid = _row(conn, account="Mainacc#NA1", status="unmatched")
    assert mid in _claimable_ids("AltTwo#NA1")
    with db.get_conn() as conn:
        db.mark_claim_checked(conn, mid, "AltTwo#NA1")
    assert mid not in _claimable_ids("AltTwo#NA1")           # not for the account we tried
    assert mid in _claimable_ids("AltOne#NA1")                 # but still open for a different one


# --- claim_reprocess decision logic (persist only on a real match) --------------------------

def _fake_load(monkeypatch, exists=True):
    monkeypatch.setattr(pipeline, "_load_for_reprocess",
                        lambda mid: (("C:/v/x.mp4", object()) if exists else (None, None)))
    committed = {}
    monkeypatch.setattr(pipeline, "_commit_reprocess", lambda mid, fields: committed.setdefault(mid, fields))
    return committed


def test_claim_reprocess_persists_only_on_match(monkeypatch):
    committed = _fake_load(monkeypatch)
    monkeypatch.setattr(pipeline, "_derive_fields",
                        lambda *a: {"riot_match_id": "NA1_9", "status": "ready", "account": "AltTwo#NA1"})
    assert pipeline.claim_reprocess(1) == "matched"
    assert 1 in committed  # the match was written


def test_claim_reprocess_leaves_miss_untouched(monkeypatch):
    committed = _fake_load(monkeypatch)
    monkeypatch.setattr(pipeline, "_derive_fields", lambda *a: {"status": "awaiting_match", "account": "AltTwo#NA1"})
    assert pipeline.claim_reprocess(1) == "no_match"
    assert committed == {}  # a non-match must NOT overwrite the row (don't steal another acct's clip)


def test_claim_reprocess_reports_api_error(monkeypatch):
    committed = _fake_load(monkeypatch)
    monkeypatch.setattr(pipeline, "_derive_fields", lambda *a: {"status": "needs_attention", "account": "m"})
    assert pipeline.claim_reprocess(1) == "error"  # so the caller won't mark it permanently tried
    assert committed == {}


def test_claim_reprocess_handles_missing_file(monkeypatch):
    _fake_load(monkeypatch, exists=False)
    assert pipeline.claim_reprocess(1) == "gone"
