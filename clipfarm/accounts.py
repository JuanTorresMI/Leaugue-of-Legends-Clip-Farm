"""Which Riot account we're currently farming clips for.

Account identity (Riot ID + region/platform) is *config*, not a secret, so it lives in a
small JSON store (data/accounts.json) rather than .env -- that's what lets it be switched
live from the dashboard without editing files or restarting. The .env RIOT_* values seed
the store on first run and remain the fallback if the file is ever missing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from clipfarm.config import get_settings

logger = logging.getLogger(__name__)

# platform routing value -> regional routing value (Match-V5 / Account-V1).
_PLATFORM_TO_REGION = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas", "oc1": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "kr": "asia", "jp1": "asia",
}


def region_for_platform(platform: str, default: str = "americas") -> str:
    return _PLATFORM_TO_REGION.get(platform.lower(), default)


@dataclass(frozen=True)
class Account:
    game_name: str
    tag_line: str
    platform: str  # na1, euw1, kr, ...
    region: str  # americas, europe, asia

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


def _store_path() -> Path:
    return get_settings().project_root / "data" / "accounts.json"


def _seed_from_env() -> Account:
    s = get_settings().secrets
    return Account(
        game_name=s.riot_game_name,
        tag_line=s.riot_tag_line,
        platform=s.riot_platform,
        region=s.riot_region,
    )


def _load() -> dict:
    path = _store_path()
    if not path.exists():
        seed = _seed_from_env()
        data = {"current": seed.riot_id, "accounts": {seed.riot_id: asdict(seed)}}
        _save(data)
        return data
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def current_account() -> Account:
    data = _load()
    acct = data["accounts"][data["current"]]
    return Account(**acct)


def list_accounts() -> list[Account]:
    data = _load()
    return [Account(**a) for a in data["accounts"].values()]


def upsert_account(game_name: str, tag_line: str, platform: str, region: str | None = None) -> Account:
    """Add or update a saved account (does not switch to it). Returns the stored Account."""
    account = Account(
        game_name=game_name,
        tag_line=tag_line,
        platform=platform,
        region=region or region_for_platform(platform),
    )
    data = _load()
    data["accounts"][account.riot_id] = asdict(account)
    _save(data)
    return account


def set_current(riot_id: str) -> Account:
    data = _load()
    if riot_id not in data["accounts"]:
        raise KeyError(f"Unknown account: {riot_id}")
    data["current"] = riot_id
    _save(data)
    return current_account()
