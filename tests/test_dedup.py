from types import SimpleNamespace

from clipfarm.media.edit import hook_text_for
from clipfarm.publishers import dedup


def test_file_content_hash_stable_and_distinct(tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"the same bytes" * 100)
    b.write_bytes(b"the same bytes" * 100)
    c = tmp_path / "c.mp4"
    c.write_bytes(b"different content entirely" * 100)

    assert dedup.file_content_hash(a) == dedup.file_content_hash(b)  # identical bytes -> same key
    assert dedup.file_content_hash(a) != dedup.file_content_hash(c)
    assert dedup.file_content_hash(tmp_path / "missing.mp4") is None


def test_content_keys_full_game():
    row = {"content_hash": "abc", "riot_match_id": "NA1_1", "kind": "full_game", "highlight_ms": None}
    keys = dedup.content_keys(row)
    assert "file:abc" in keys
    assert "match:NA1_1" in keys
    assert not any(k.startswith("clip:") for k in keys)


def test_content_keys_clip_with_highlight():
    row = {"content_hash": "abc", "riot_match_id": "NA1_1", "kind": "clip", "highlight_ms": 840000}
    keys = dedup.content_keys(row)
    assert "clip:NA1_1:840000" in keys  # a specific kill, so different kills stay distinct
    assert "file:abc" in keys
    assert "match:NA1_1" not in keys  # a clip is not a full-game upload


def test_content_keys_clip_without_highlight_relies_on_file_hash():
    row = {"content_hash": "abc", "riot_match_id": "NA1_1", "kind": "clip", "highlight_ms": None}
    keys = dedup.content_keys(row)
    assert keys == ["file:abc"]  # no false dedup between two highlight-less clips of one game


def test_content_keys_unmatched_file_only():
    row = {"content_hash": "abc", "riot_match_id": None, "kind": "clip", "highlight_ms": None}
    assert dedup.content_keys(row) == ["file:abc"]


def test_content_keys_accepts_objects_too():
    row = SimpleNamespace(content_hash="h", riot_match_id="NA1_9", kind="full_game", highlight_ms=None)
    assert "match:NA1_9" in dedup.content_keys(row)


def test_hook_text_from_streak_and_champion():
    assert hook_text_for({"kind": "clip", "champion": "JarvanIV", "kill_streak": 5}) == "PENTAKILL!"
    assert hook_text_for({"kind": "clip", "champion": "JarvanIV", "kill_streak": 3}) == "TRIPLE KILL!"
    assert hook_text_for({"kind": "clip", "champion": "JarvanIV", "kill_streak": 1}) == "JARVAN IV"
    assert hook_text_for({"kind": "full_game", "champion": "JarvanIV", "kill_streak": None}) is None
