"""Thin wrapper around RiotWatcher with loud, distinct failure on an expired dev API key."""
from __future__ import annotations

import logging
from functools import lru_cache

from riotwatcher import ApiError, LolWatcher, RiotWatcher

logger = logging.getLogger(__name__)


class RiotKeyExpiredError(RuntimeError):
    """Raised when Riot rejects the API key (401/403) -- the dev key needs manual refresh."""


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) if response is not None else None


class RiotClient:
    def __init__(self) -> None:
        from clipfarm import riot_key
        from clipfarm.accounts import current_account  # local import avoids a config import cycle

        account = current_account()
        key = riot_key.current_key()
        # Account-V1 (by_riot_id) lives on RiotWatcher; Match-V5 lives on LolWatcher.
        self._riot_watcher = RiotWatcher(key)
        self._lol_watcher = LolWatcher(key)
        self._region = account.region
        self._platform = account.platform
        self._game_name = account.game_name
        self._tag_line = account.tag_line
        self._puuid: str | None = None
        self._rank: str | None = None
        self._rank_fetched = False

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            status = _status_code(exc)
            if status in (401, 403):
                raise RiotKeyExpiredError(
                    "Riot API key was rejected (401/403) -- get a fresh key from "
                    "https://developer.riotgames.com and paste it into the dashboard's Riot Key "
                    "panel (dev keys expire every ~24h)."
                ) from exc
            raise

    def get_puuid(self) -> str:
        if self._puuid is None:
            account = self._call(
                self._riot_watcher.account.by_riot_id, self._region, self._game_name, self._tag_line
            )
            self._puuid = account["puuid"]
        return self._puuid

    def get_rank(self) -> str | None:
        """Current Ranked Solo/Duo rank as a display string, e.g. "Emerald II".

        Fetched once per client lifetime (rank moves slowly relative to a session).
        Returns None for unranked accounts or if the lookup fails -- rank is a
        nice-to-have for titles, never a reason to fail processing.
        """
        if self._rank_fetched:
            return self._rank
        self._rank_fetched = True
        try:
            entries = self._call(self._lol_watcher.league.by_puuid, self._platform, self.get_puuid())
            solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
            if solo:
                tier = solo["tier"].capitalize()
                # Apex tiers have no division
                division = solo.get("rank", "")
                apex = tier in ("Master", "Grandmaster", "Challenger")
                self._rank = tier if apex else f"{tier} {division}".strip()
        except RiotKeyExpiredError:
            raise
        except Exception:  # noqa: BLE001 -- rank enriches titles; its failure must not block ingest
            logger.warning("Rank lookup failed; continuing without rank", exc_info=True)
        return self._rank

    def get_match_ids_in_window(self, start_time: int, end_time: int, count: int = 20) -> list[str]:
        """start_time/end_time are epoch seconds."""
        puuid = self.get_puuid()
        return self._call(
            self._lol_watcher.match.matchlist_by_puuid,
            self._region,
            puuid,
            start_time=start_time,
            end_time=end_time,
            count=count,
        )

    def get_match_detail(self, match_id: str) -> dict:
        return self._call(self._lol_watcher.match.by_id, self._region, match_id)

    def get_timeline(self, match_id: str) -> dict:
        return self._call(self._lol_watcher.match.timeline_by_match, self._region, match_id)

    @property
    def riot_id(self) -> str:
        return f"{self._game_name}#{self._tag_line}"

    def check_key(self) -> bool:
        """Health check for the CLI/dashboard. Returns True if the key is valid."""
        try:
            self.get_puuid()
            return True
        except RiotKeyExpiredError:
            return False


class AccountNotFoundError(RuntimeError):
    """The Riot ID doesn't resolve -- likely a typo in the name or tagline."""


def validate_key(key: str) -> bool:
    """True if Riot accepts this key (resolves the current account). Used to validate a
    pasted key from the dashboard before saving it."""
    from clipfarm.accounts import current_account

    account = current_account()
    watcher = RiotWatcher(key)
    try:
        watcher.account.by_riot_id(account.region, account.game_name, account.tag_line)
        return True
    except ApiError as exc:
        status = _status_code(exc)
        if status in (401, 403):
            return False
        if status == 404:
            return True  # key works; the account name just didn't resolve
        raise


def validate_account(game_name: str, tag_line: str, region: str) -> str:
    """Resolve a Riot ID to its PUUID to confirm it exists before we switch to it.
    Raises RiotKeyExpiredError (bad key) or AccountNotFoundError (bad Riot ID)."""
    from clipfarm import riot_key

    watcher = RiotWatcher(riot_key.current_key())
    try:
        account = watcher.account.by_riot_id(region, game_name, tag_line)
        return account["puuid"]
    except ApiError as exc:
        status = _status_code(exc)
        if status in (401, 403):
            raise RiotKeyExpiredError(
                "Riot API key was rejected -- update it in the dashboard's Riot Key panel."
            ) from exc
        if status == 404:
            raise AccountNotFoundError(f"No Riot account found for {game_name}#{tag_line} in {region}.") from exc
        raise


@lru_cache
def get_riot_client() -> RiotClient:
    return RiotClient()


def reset_riot_client() -> None:
    """Drop the cached client so the next call rebuilds it against the current account
    (and re-reads a freshly-refreshed API key). Called after switching accounts."""
    get_riot_client.cache_clear()
