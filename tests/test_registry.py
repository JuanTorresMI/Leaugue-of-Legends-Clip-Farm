from clipfarm.publishers import registry


def test_youtube_always_enabled():
    assert "youtube" in registry.enabled_platforms("clip")
    assert "youtube" in registry.enabled_platforms("full_game")


def test_get_returns_platform_or_none():
    assert registry.get("youtube").name == "youtube"
    assert registry.get("nope") is None


def test_enabled_platforms_respects_kind_and_flag(monkeypatch):
    # A platform only routed to clips shouldn't appear for full games.
    fake = registry.Platform(
        name="tiktok", kinds=("clip",), is_enabled=lambda: True, publish=lambda mf: "x"
    )
    monkeypatch.setattr(registry, "_REGISTRY", (*registry._REGISTRY, fake))
    assert "tiktok" in registry.enabled_platforms("clip")
    assert "tiktok" not in registry.enabled_platforms("full_game")


def test_enabled_platforms_respects_disabled(monkeypatch):
    fake = registry.Platform(
        name="off", kinds=("clip",), is_enabled=lambda: False, publish=lambda mf: "x"
    )
    monkeypatch.setattr(registry, "_REGISTRY", (*registry._REGISTRY, fake))
    assert "off" not in registry.enabled_platforms("clip")
