"""Fetch and cache champion art from Riot's Data Dragon CDN (free, no auth).

Match-V5's internal champion names (e.g. "JarvanIV", "MonkeyKing") are exactly the file
identifiers Data Dragon uses, so no name mapping is needed for asset paths. Assets are
cached under data/ddragon/ so we hit the CDN at most once per champion per art type.
"""
from __future__ import annotations

import logging
import urllib.request
from functools import lru_cache
from pathlib import Path

from clipfarm.config import get_settings

logger = logging.getLogger(__name__)

_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
_USER_AGENT = "LeagueClipFarm/1.0"


def _cache_dir() -> Path:
    d = get_settings().project_root / "data" / "ddragon"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(url: str, dest: Path) -> Path | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())
        return dest
    except Exception:  # noqa: BLE001 -- art is an enhancement; never fail the pipeline over it
        logger.warning("Data Dragon fetch failed: %s", url, exc_info=True)
        return None


@lru_cache(maxsize=1)
def latest_version() -> str:
    try:
        req = urllib.request.Request(_VERSIONS_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            import json

            return json.loads(resp.read())[0]
    except Exception:  # noqa: BLE001
        logger.warning("Could not fetch Data Dragon version list; falling back", exc_info=True)
        return "15.1.1"


def loading_art(champion: str) -> Path | None:
    """Portrait loading-screen art (308x560) -- best fit for a thumbnail focal cutout."""
    dest = _cache_dir() / f"loading_{champion}.jpg"
    if dest.exists():
        return dest
    return _download(f"https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{champion}_0.jpg", dest)


def splash_art(champion: str) -> Path | None:
    """Full landscape splash art (1215x717) -- good for backgrounds."""
    dest = _cache_dir() / f"splash_{champion}.jpg"
    if dest.exists():
        return dest
    return _download(f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{champion}_0.jpg", dest)


def square_icon(champion: str) -> Path | None:
    """Square champion icon (120x120) -- small badge use."""
    dest = _cache_dir() / f"square_{champion}.png"
    if dest.exists():
        return dest
    version = latest_version()
    return _download(
        f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion}.png", dest
    )
