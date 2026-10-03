from types import SimpleNamespace

from PIL import Image

from clipfarm.media import composite
from clipfarm.media.composite import ThumbnailSpec, generate_composite, spec_for_clip, spec_for_full_game


def _match(**overrides):
    defaults = dict(
        champion="JarvanIV",
        kda="12/4/6",
        win=True,
        queue_type="Ranked Solo/Duo",
        role="Jungle",
        patch="26.13",
        opponent_champion="Viego",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_spec_for_full_game_win_uses_display_names():
    spec = spec_for_full_game(_match(), rank="Emerald II")
    assert spec.hook == "WIN"
    assert spec.champion_display == "Jarvan IV"
    assert "vs Viego" in spec.subtitle
    assert "Emerald" in spec.subtitle
    assert spec.accent == composite.GREEN


def test_spec_for_full_game_loss_leads_with_the_matchup():
    # "LOSS" on a thumbnail repels the click; a lost game shows the matchup badge instead.
    spec = spec_for_full_game(_match(win=False), rank=None)
    assert spec.hook == "vs Viego"
    assert spec.accent == composite.BLUE
    assert spec_for_full_game(_match(win=False, opponent_champion=None), rank=None).hook == "FULL GAME"


def test_spec_from_row_loss_never_says_loss():
    row = {"kind": "full_game", "champion": "Vi", "win": 0, "kda": "2/9/4", "rank": None, "role": "Jungle",
           "opponent_champion": "Viego", "queue_type": "Ranked Solo/Duo", "kill_streak": None}
    spec = composite.spec_from_row(row)
    assert "LOSS" not in spec.hook
    assert "2/9/4" not in spec.subtitle            # a losing score is never on the artwork
    assert spec.subtitle.count("Viego") == 0        # the badge already says "vs Viego"
    assert "Jungle" in spec.subtitle


def test_spec_for_clip_multikill_is_gold():
    highlight = SimpleNamespace(kill_streak=3, champion="JarvanIV", victim_champions=[])
    spec = spec_for_clip(_match(), highlight, rank="Emerald II")
    assert spec.hook == "Triple Kill"
    assert spec.accent == composite.GOLD


def test_spec_from_row_full_game():
    row = {
        "kind": "full_game",
        "champion": "JarvanIV",
        "win": 1,
        "kda": "12/4/6",
        "rank": "Emerald III",
        "role": "Jungle",
        "opponent_champion": "Viego",
        "queue_type": "Ranked Solo/Duo",
        "kill_streak": None,
    }
    spec = composite.spec_from_row(row)
    assert spec.hook == "WIN"
    assert spec.champion_display == "Jarvan IV"
    assert "vs Viego" in spec.subtitle
    assert "Emerald III Jungle" in spec.subtitle


def test_spec_from_row_multikill_clip():
    row = {
        "kind": "clip",
        "champion": "Vi",
        "win": None,
        "kda": None,
        "rank": "Emerald III",
        "role": "Jungle",
        "opponent_champion": None,
        "queue_type": "Ranked Solo/Duo",
        "kill_streak": 2,
    }
    spec = composite.spec_from_row(row)
    assert spec.hook == "Double Kill"
    assert spec.accent == composite.GOLD


def test_spec_from_row_unmatched_returns_none():
    row = {"kind": "clip", "champion": None}
    assert composite.spec_from_row(row) is None


def test_generate_composite_degrades_without_network(monkeypatch, tmp_path):
    # Force the champion-art fetchers to fail so we exercise the graceful path.
    monkeypatch.setattr(composite.ddragon, "loading_art", lambda champ: None)
    monkeypatch.setattr(composite.ddragon, "splash_art", lambda champ: None)

    frame = tmp_path / "frame.png"
    Image.new("RGB", (1920, 1080), (40, 60, 40)).save(frame)

    out = tmp_path / "thumb.jpg"
    spec = ThumbnailSpec(champion="JarvanIV", hook="WIN", subtitle="12/4/6", champion_display="Jarvan IV")
    generate_composite(spec, frame, out)

    assert out.exists()
    with Image.open(out) as img:
        assert img.size == (composite.W, composite.H)
