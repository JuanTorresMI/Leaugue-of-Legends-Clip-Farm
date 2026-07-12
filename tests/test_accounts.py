import pytest

from clipfarm import accounts


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    # Point the accounts store at a temp file and stub the .env seed.
    store = tmp_path / "accounts.json"
    monkeypatch.setattr(accounts, "_store_path", lambda: store)

    class _Secrets:
        riot_game_name = "Mainacc"
        riot_tag_line = "NA1"
        riot_platform = "na1"
        riot_region = "americas"

    class _Settings:
        secrets = _Secrets()

    monkeypatch.setattr(accounts, "get_settings", lambda: _Settings())
    yield


def test_region_for_platform():
    assert accounts.region_for_platform("na1") == "americas"
    assert accounts.region_for_platform("euw1") == "europe"
    assert accounts.region_for_platform("kr") == "asia"
    assert accounts.region_for_platform("unknown") == "americas"  # sane default


def test_seeds_from_env_on_first_load():
    current = accounts.current_account()
    assert current.riot_id == "Mainacc#NA1"
    assert current.platform == "na1"
    assert current.region == "americas"


def test_add_and_switch_account():
    accounts.upsert_account("Smurfy", "SMRF", "euw1")
    assert {a.riot_id for a in accounts.list_accounts()} == {"Mainacc#NA1", "Smurfy#SMRF"}

    accounts.set_current("Smurfy#SMRF")
    current = accounts.current_account()
    assert current.riot_id == "Smurfy#SMRF"
    assert current.region == "europe"  # derived from euw1


def test_switch_to_unknown_raises():
    with pytest.raises(KeyError):
        accounts.set_current("Nobody#XXX")
