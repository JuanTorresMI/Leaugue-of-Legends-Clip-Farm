from clipfarm.media import edit


def test_fade_suffix_skips_when_too_short():
    # duration below fade_in + fade_out + 0.5 -> no fades (avoids overlapping fades)
    assert edit._fade_suffix("video", 0.5, 0.4, 0.6) == ""


def test_fade_suffix_video_and_audio():
    v = edit._fade_suffix("video", 30.0, 0.4, 0.6)
    a = edit._fade_suffix("audio", 30.0, 0.4, 0.6)
    assert "fade=t=in:st=0:d=0.40" in v and "fade=t=out:st=29.40:d=0.60" in v
    assert a.startswith(",afade=t=in")  # audio uses the afade filter


def test_pick_music_empty_dir(tmp_path, monkeypatch):
    empty = tmp_path / "music"
    empty.mkdir()

    class _Ed:
        music_dir = empty

    class _Settings:
        editing = _Ed()

    monkeypatch.setattr(edit, "get_settings", lambda: _Settings())
    assert edit.pick_music() is None


def test_pick_music_returns_a_track(tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.mp3").write_bytes(b"not really audio but has the extension")
    (music / "notes.txt").write_text("ignore me")

    class _Ed:
        music_dir = music

    class _Settings:
        editing = _Ed()

    monkeypatch.setattr(edit, "get_settings", lambda: _Settings())
    picked = edit.pick_music()
    assert picked is not None and picked.name == "song.mp3"  # only the audio file is eligible
