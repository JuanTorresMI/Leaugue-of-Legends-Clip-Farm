import pytest

from clipfarm import fb_settings


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    store = tmp_path / "facebook.json"
    monkeypatch.setattr(fb_settings, "_store_path", lambda: store)

    class _Secrets:
        fb_page_id = None
        fb_page_access_token = None

    class _Facebook:
        enabled = False

    class _Settings:
        secrets = _Secrets()
        facebook = _Facebook()

    monkeypatch.setattr(fb_settings, "get_settings", lambda: _Settings())
    yield


def test_defaults_to_unconfigured_and_disabled():
    s = fb_settings.current()
    assert s.page_id is None and s.access_token is None
    assert s.enabled is False
    assert s.configured is False
    assert fb_settings.is_enabled() is False


def test_save_and_reload():
    fb_settings.save(fb_settings.FacebookSettings(page_id="123", access_token="tok", enabled=True))
    s = fb_settings.current()
    assert s.page_id == "123" and s.access_token == "tok" and s.enabled is True
    assert s.configured is True
    assert fb_settings.is_enabled() is True


def test_enabled_but_unconfigured_is_not_active():
    # enabled flag on but no credentials -> not actually usable
    fb_settings.save(fb_settings.FacebookSettings(page_id=None, access_token=None, enabled=True))
    assert fb_settings.is_enabled() is False
