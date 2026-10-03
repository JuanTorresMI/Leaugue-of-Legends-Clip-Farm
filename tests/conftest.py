import json
from pathlib import Path

import pytest

from clipfarm import config

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch, tmp_path):
    """Make every test independent of the developer's machine.

    - Required secrets get dummy values and the real .env is ignored, so the suite runs on a
      fresh clone (CI) and never touches a real Riot/Facebook credential.
    - PROJECT_ROOT is pointed at a per-test temp dir, so the JSON stores (accounts.json,
      facebook.json, riot_key.txt, ...) and the default database land there instead of in the
      repo's data/ folder.
    - The settings cache is cleared around each test so no test sees another's settings.
    """
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test-key")
    monkeypatch.setenv("RIOT_GAME_NAME", "TestPlayer")
    monkeypatch.setenv("RIOT_TAG_LINE", "NA1")
    monkeypatch.setitem(config.Secrets.model_config, "env_file", None)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


class FakeRiotClient:
    """Duck-types clipfarm.riot.client.RiotClient using fixture JSON instead of live API calls."""

    def __init__(self, match_detail: dict, timeline: dict, puuid: str = "test-puuid-1234"):
        self._match_detail = match_detail
        self._timeline = timeline
        self._puuid = puuid

    def get_puuid(self) -> str:
        return self._puuid

    def get_match_ids_in_window(self, start_time: int, end_time: int, count: int = 20) -> list[str]:
        return [self._match_detail["metadata"]["matchId"]]

    def get_match_detail(self, match_id: str) -> dict:
        return self._match_detail

    def get_timeline(self, match_id: str) -> dict:
        return self._timeline


@pytest.fixture
def sample_match_detail() -> dict:
    with (FIXTURES_DIR / "sample_match_detail.json").open() as f:
        return json.load(f)


@pytest.fixture
def sample_timeline() -> dict:
    with (FIXTURES_DIR / "sample_timeline.json").open() as f:
        return json.load(f)


@pytest.fixture
def fake_riot_client(sample_match_detail, sample_timeline) -> FakeRiotClient:
    return FakeRiotClient(sample_match_detail, sample_timeline)
