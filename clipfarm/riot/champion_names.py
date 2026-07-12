"""Riot's internal champion identifiers vs. human-facing display names.

Match-V5 returns internal names like "JarvanIV" or "MonkeyKing". Titles and descriptions
should read "Jarvan IV" and "Wukong". Hashtags/tags need a compact, symbol-free variant.
Only champions whose internal name differs from their display name are listed; everything
else passes through unchanged.
"""
from __future__ import annotations

_DISPLAY_NAMES = {
    "AurelionSol": "Aurelion Sol",
    "Belveth": "Bel'Veth",
    "Chogath": "Cho'Gath",
    "DrMundo": "Dr. Mundo",
    "FiddleSticks": "Fiddlesticks",
    "JarvanIV": "Jarvan IV",
    "Kaisa": "Kai'Sa",
    "Khazix": "Kha'Zix",
    "KogMaw": "Kog'Maw",
    "KSante": "K'Sante",
    "Leblanc": "LeBlanc",
    "LeeSin": "Lee Sin",
    "MasterYi": "Master Yi",
    "MissFortune": "Miss Fortune",
    "MonkeyKing": "Wukong",
    "Nunu": "Nunu & Willump",
    "RekSai": "Rek'Sai",
    "Renata": "Renata Glasc",
    "TahmKench": "Tahm Kench",
    "TwistedFate": "Twisted Fate",
    "Velkoz": "Vel'Koz",
    "XinZhao": "Xin Zhao",
}


def display_name(internal_name: str) -> str:
    """Human-facing name for titles/descriptions, e.g. JarvanIV -> Jarvan IV."""
    return _DISPLAY_NAMES.get(internal_name, internal_name)


def tag_name(internal_name: str) -> str:
    """Compact, symbol-free variant for hashtags, e.g. Kai'Sa -> KaiSa, Wukong -> Wukong."""
    return "".join(ch for ch in display_name(internal_name) if ch.isalnum())
