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


def _edit_settings(tmp_path, **overrides):
    class _Ed:
        enabled = True
        music_dir = tmp_path / "no-music"
        clip_music_volume = 0.18
        normalize_audio = True
        fade_in_seconds = 0.0
        fade_out_seconds = 0.0
        hook_caption = False
        hook_seconds = 2.5
        watermark_text = ""
        subscribe_cta_text = ""
        subscribe_cta_seconds = 2.5
        like_cta_text = ""
        like_cta_seconds = 2.0

    for key, value in overrides.items():
        setattr(_Ed, key, value)

    class _Ascent:
        ffmpeg_path = "ffmpeg"

    class _Settings:
        editing = _Ed()
        ascent = _Ascent()
        project_root = tmp_path

    return _Settings()


def test_hook_text_prefers_the_streak_then_the_champion_and_never_a_generic_line():
    assert edit.hook_text_for({"kind": "clip", "champion": "JarvanIV", "kill_streak": 5}) == "PENTAKILL"
    assert edit.hook_text_for({"kind": "clip", "champion": "JarvanIV", "kill_streak": 3}) == "TRIPLE KILL"
    assert edit.hook_text_for({"kind": "clip", "champion": "JarvanIV", "kill_streak": 1}) == "JARVAN IV"
    # No kill and no champion: burn nothing rather than a "WATCH THIS" that screams template.
    assert edit.hook_text_for({"kind": "clip", "champion": None, "kill_streak": None}) is None
    assert edit.hook_text_for({"kind": "full_game", "champion": "Vi", "kill_streak": None}) is None


def test_render_pill_png(tmp_path, monkeypatch):
    class _Settings:
        project_root = tmp_path

    monkeypatch.setattr(edit, "get_settings", lambda: _Settings())
    out = edit._render_subscribe_png("Subscribe for more", tmp_path / "cta.png")
    assert out is not None and out.exists()
    from PIL import Image

    with Image.open(out) as img:
        assert img.size == (1080, 1920)
        # Something opaque-ish sits in the lower third (the pill), nothing at the very top.
        assert img.getpixel((540, int(1920 * 0.70)))[3] > 0
        assert img.getpixel((540, 40))[3] == 0


def test_prepare_clip_filter_includes_watermark(tmp_path, monkeypatch):
    """The watermark PNG becomes a bounded looped input overlaid for the whole clip."""
    captured = {}
    monkeypatch.setattr(edit, "get_settings",
                        lambda: _edit_settings(tmp_path, watermark_text="@YourChannel"))
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


def test_prepare_clip_flashes_subscribe_cta_at_the_end(tmp_path, monkeypatch):
    """The subscribe ask is enabled only over the clip's final subscribe_cta_seconds."""
    captured = {}
    monkeypatch.setattr(edit, "get_settings",
                        lambda: _edit_settings(tmp_path, subscribe_cta_text="SUBSCRIBE FOR MORE"))
    monkeypatch.setattr(edit, "duration_seconds", lambda p: 34.3)
    monkeypatch.setattr(edit, "_run", lambda args: captured.setdefault("args", args))

    edit.prepare_clip(tmp_path / "in.mp4", tmp_path / "out.mp4")
    fc = captured["args"][captured["args"].index("-filter_complex") + 1]
    assert "overlay=0:0:enable='gte(t,31.80)'" in fc  # 34.3 - 2.5: only the final seconds
    # The ask fades in at its start and out before the clip ends -- never a hard pop.
    assert "fade=t=in:st=31.80:d=0.25:alpha=1" in fc
    assert "fade=t=out:st=34.00:d=0.30:alpha=1" in fc


def test_prepare_clip_hook_caption_fades_in_and_out(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(edit, "get_settings", lambda: _edit_settings(tmp_path, hook_caption=True))
    monkeypatch.setattr(edit, "duration_seconds", lambda p: 34.3)
    monkeypatch.setattr(edit, "_run", lambda args: captured.setdefault("args", args))

    edit.prepare_clip(tmp_path / "in.mp4", tmp_path / "out.mp4", hook_text="TRIPLE KILL")
    args = captured["args"]
    fc = args[args.index("-filter_complex") + 1]
    assert "[1:v]format=rgba,fade=t=in:st=0.00:d=0.25:alpha=1,fade=t=out:st=2.20:d=0.30:alpha=1[t0]" in fc
    assert "[v0][t0]overlay=0:0:enable='lte(t,2.50)'[v]" in fc
    assert not (tmp_path / "out.caption.png").exists()  # temp PNG cleaned up after the render


def test_prepare_clip_skips_cta_on_short_clips(tmp_path, monkeypatch):
    """Too short for hook + CTA to coexist -> no subscribe overlay at all."""
    captured = {}
    monkeypatch.setattr(edit, "get_settings",
                        lambda: _edit_settings(tmp_path, subscribe_cta_text="SUBSCRIBE FOR MORE"))
    monkeypatch.setattr(edit, "duration_seconds", lambda p: 6.0)  # under 2.5 + 2.5 + 3
    monkeypatch.setattr(edit, "_run", lambda args: captured.setdefault("args", args))

    edit.prepare_clip(tmp_path / "in.mp4", tmp_path / "out.mp4")
    fc = captured["args"][captured["args"].index("-filter_complex") + 1]
    assert "gte(t," not in fc


def test_prepare_clip_flashes_like_ask_mid_clip(tmp_path, monkeypatch):
    """The like ask shows for a brief window at ~55% of the clip, clear of hook and CTA."""
    captured = {}
    monkeypatch.setattr(edit, "get_settings",
                        lambda: _edit_settings(tmp_path, like_cta_text="LIKE IF THAT WAS CLEAN",
                                               subscribe_cta_text="SUBSCRIBE FOR MORE"))
    monkeypatch.setattr(edit, "duration_seconds", lambda p: 34.3)
    monkeypatch.setattr(edit, "_run", lambda args: captured.setdefault("args", args))

    edit.prepare_clip(tmp_path / "in.mp4", tmp_path / "out.mp4")
    fc = captured["args"][captured["args"].index("-filter_complex") + 1]
    assert "between(t,18.86,20.86)" in fc          # 34.3 * 0.55, for like_cta_seconds
    assert "overlay=0:0:enable='gte(t,31.80)'" in fc  # subscribe ask still on its own window


def test_prepare_clip_skips_like_ask_when_windows_would_collide(tmp_path, monkeypatch):
    """On a short clip the mid-point sits inside the hook/CTA windows -> no like overlay."""
    captured = {}
    monkeypatch.setattr(edit, "get_settings",
                        lambda: _edit_settings(tmp_path, like_cta_text="LIKE IF THAT WAS CLEAN",
                                               subscribe_cta_text="SUBSCRIBE FOR MORE"))
    monkeypatch.setattr(edit, "duration_seconds", lambda p: 9.0)  # midpoint 4.95, CTA begins 6.5
    monkeypatch.setattr(edit, "_run", lambda args: captured.setdefault("args", args))

    edit.prepare_clip(tmp_path / "in.mp4", tmp_path / "out.mp4")
    fc = captured["args"][captured["args"].index("-filter_complex") + 1]
    assert "between(t," not in fc


def test_prepared_clip_renders_once_under_concurrency(tmp_path, monkeypatch):
    """Two platforms publishing the same clip at once must not both render it (the WinError 32
    file-collision that stopped Facebook uploads). The per-clip lock makes it render once; the
    second caller reuses the cache."""
    import threading
    import time

    cache_dir = tmp_path / "data" / "converted"
    cache_dir.mkdir(parents=True)

    class _Ed:
        enabled = True

    class _Settings:
        editing = _Ed()
        project_root = tmp_path

    monkeypatch.setattr(edit, "get_settings", lambda: _Settings())

    renders = []

    def fake_prepare(video_path, cache_path, hook_text=None):
        renders.append(cache_path)
        time.sleep(0.2)          # hold the lock long enough for the other thread to contend
        cache_path.write_bytes(b"rendered")
        return cache_path

    monkeypatch.setattr(edit, "prepare_clip", fake_prepare)

    src = tmp_path / "clip.mp4"
    results = []

    def worker():
        results.append(edit.prepared_clip(src, "HOOK"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(renders) == 1                       # rendered exactly once despite 2 concurrent callers
    assert len({str(r) for r in results}) == 1     # both got the same cached file
