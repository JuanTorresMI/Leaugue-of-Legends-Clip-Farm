import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
