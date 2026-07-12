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


def test_render_watermark_png(tmp_path, monkeypatch):
    class _Settings:
        project_root = tmp_path  # no Anton font here -> falls back to Pillow's default font

    monkeypatch.setattr(edit, "get_settings", lambda: _Settings())
    out = edit._render_watermark_png("@YourChannel", tmp_path / "wm.png")
    assert out is not None and out.exists() and out.stat().st_size > 0


def test_prepare_clip_filter_includes_watermark(tmp_path, monkeypatch):
    """The watermark PNG becomes a bounded looped input overlaid for the whole clip."""
    captured = {}

    class _Ed:
        enabled = True
        music_dir = tmp_path / "no-music"
        clip_music_volume = 0.18
        normalize_audio = True
        fade_in_seconds = 0.0
        fade_out_seconds = 0.0
        hook_caption = False
        hook_seconds = 2.5
        watermark_text = "@YourChannel"

    class _Ascent:
        ffmpeg_path = "ffmpeg"

    class _Settings:
        editing = _Ed()
        ascent = _Ascent()
        project_root = tmp_path

    monkeypatch.setattr(edit, "get_settings", lambda: _Settings())
    monkeypatch.setattr(edit, "duration_seconds", lambda p: 34.3)

    def fake_run(args):
        captured["args"] = args

    monkeypatch.setattr(edit, "_run", fake_run)
    edit.prepare_clip(tmp_path / "in.mp4", tmp_path / "out.mp4")

    args = captured["args"]
    fc = args[args.index("-filter_complex") + 1]
    assert "[1:v]overlay=0:0" in fc  # watermark input overlaid over the whole clip
    assert "-t" in args and "34.30" in args  # looped PNG bounded to the clip duration
    assert "fade" not in fc  # fades are off -> seamless loop
