"""Turn match/timeline data into draft titles, descriptions, hashtags, and YouTube tags.

Everything here is a *draft* -- the dashboard allows editing before anything posts.

Discoverability principles baked in:
- The hook (champion + what happened) leads the title; rank/role context follows.
- Kill-clip titles rotate through three A/B hook styles ("hype" / "wait" / "why"),
  picked deterministically per clip (seeded by match id + kill timestamp) so a batch of
  uploads doesn't read like a bot wrote it, regenerating drafts is stable, and — because the
  chosen style is stored on the row — the metrics dashboard can measure which style earns
  the most views and retention.
- Descriptions front-load searchable phrasing in the first two lines (what YouTube weighs
  most), then a hashtag block.
- A separate `tags` list feeds YouTube's tags API field (500-char budget) with multi-word
  search phrases that don't belong in visible hashtags.
"""
from __future__ import annotations

import zlib
from datetime import datetime

from clipfarm.jobs.models import ClipHighlight, DraftMetadata, MatchContext
from clipfarm.riot.champion_names import display_name, tag_name

KILL_STREAK_NAMES = {1: "Kill", 2: "Double Kill", 3: "Triple Kill", 4: "Quadra Kill", 5: "Penta Kill"}

_SINGLE_KILL_VERBS = ["outplays", "picks off", "deletes", "1-SHOTS", "punishes", "catches out", "destroys"]

# Scroll-stopping adjectives + emoji for the streak hooks. Emoji noticeably lift CTR on the
# Shorts shelf; the adjective adds emotion without lying about what happened.
_STREAK_EMOJI = {2: "⚔️", 3: "🔥", 4: "😳", 5: "😱"}
_HYPE_WORDS = ["INSANE", "DISGUSTING", "UNREAL", "CRAZY", "CLEAN"]

YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_TAGS_CHAR_BUDGET = 480  # stay under YouTube's 500-char tags limit with margin

# One consistent call-to-action across every upload. Subscribers -> watch-time -> the 1,000-sub
# + watch-hour thresholds that unlock YouTube Partner monetization; a fixed CTA also trains the
# audience to expect a daily posting cadence.
def _cta(champ: str) -> str:
    return f"👉 SUBSCRIBE for daily {champ} plays — new clips every day!"


def streak_label(streak: int) -> str:
    return KILL_STREAK_NAMES.get(streak, f"{streak}-Kill Streak")


def _fit_title(title: str) -> str:
    if len(title) <= YOUTUBE_TITLE_LIMIT:
        return title
    return title[: YOUTUBE_TITLE_LIMIT - 1].rsplit(" ", 1)[0] + "…"


def _pick_verb(seed: str) -> str:
    return _SINGLE_KILL_VERBS[zlib.crc32(seed.encode()) % len(_SINGLE_KILL_VERBS)]


def _pick_hype(seed: str) -> str:
    return _HYPE_WORDS[zlib.crc32(seed.encode()) % len(_HYPE_WORDS)]


# The A/B title experiment. Each kill clip is assigned one of three hook styles; the choice is
# stored on the row (media_files.title_variant) so the metrics dashboard can compare avg views
# and retention per style and we learn which hook actually stops the scroll:
#   hype -- direct hype statement (the original style): "Draven INSANE TRIPLE KILL vs X 🔥"
#   wait -- curiosity gap / payoff tease:               "Wait for the TRIPLE KILL 🔥 Draven vs X"
#   why  -- lesson framing (LoL viewers love these):    "This is why you don't fight Draven 🔥"
_TITLE_VARIANTS = ("hype", "wait", "why")


def _pick_variant(seed: str) -> str:
    # Salted so the variant choice is independent of the verb/hype-word picks on the same seed.
    return _TITLE_VARIANTS[zlib.crc32(f"variant:{seed}".encode()) % len(_TITLE_VARIANTS)]


def _victims_phrase(victims: list[str]) -> str:
    names = [display_name(v) for v in victims]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]}, {names[1]} & more"


def _cap_tags(tags: list[str]) -> list[str]:
    """Dedupe (case-insensitive, order-preserving) and stay inside YouTube's char budget."""
    seen: set[str] = set()
    result: list[str] = []
    used = 0
    for tag in tags:
        tag = tag.strip()
        if not tag or tag.lower() in seen:
            continue
        if used + len(tag) > YOUTUBE_TAGS_CHAR_BUDGET:
            break
        seen.add(tag.lower())
        result.append(tag)
        used += len(tag)
    return result


def _tier(rank: str | None) -> str | None:
    """'Emerald II' -> 'Emerald'."""
    return rank.split()[0] if rank else None


def _base_tags(match: MatchContext, rank: str | None) -> list[str]:
    champ = display_name(match.champion).lower()
    tags = [
        "league of legends",
        "lol",
        champ,
        f"{champ} gameplay",
    ]
    if match.role:
        tags.append(f"{champ} {match.role.lower()}")
        tags.append(f"{match.role.lower()} gameplay")
    if rank:
        tags.append(f"{_tier(rank).lower()} {match.role.lower()}" if match.role else _tier(rank).lower())
    tags.append(match.queue_type.lower())
    if match.patch:
        tags.append(f"patch {match.patch}")
        tags.append(f"league of legends patch {match.patch}")
    return tags


def build_clip_metadata(
    match: MatchContext | None,
    highlight: ClipHighlight | None,
    recorded_at: datetime,
    rank: str | None = None,
) -> DraftMetadata:
    if match is None:
        return DraftMetadata(
            title=f"INSANE League of Legends Highlight 🔥 #shorts ({recorded_at:%b %d})",
            description=(
                "INSANE League of Legends highlight from my own ranked games. 🔥\n"
                f"{_cta('League')}\n\n"
                "#Shorts #LeagueOfLegends #LoL #Gaming"
            ),
            hashtags=["#Shorts", "#LeagueOfLegends", "#LoL", "#Gaming"],
            tags=["league of legends", "lol", "league of legends shorts", "lol clips",
                  "league of legends best plays", "lol montage"],
        )

    champ = display_name(match.champion)
    champ_tag = tag_name(match.champion)
    tier = _tier(rank)
    context_bits = " ".join(bit for bit in [tier, match.role] if bit)  # e.g. "Emerald Jungle"

    variant: str | None = None
    if highlight is None:
        hook = f"{champ} INSANE Highlight 🔥"
        what_happened = f"{champ} highlight from a {match.queue_type} game"
        streak_hashtag = None
        streak_tag = None
    elif highlight.kill_streak >= 5:
        seed = f"{match.match_id}:{highlight.first_kill_ms or recorded_at.isoformat()}"
        variant = _pick_variant(seed)
        hook = {
            "hype": f"{champ} 1v5 PENTAKILL 😱",
            "wait": f"Wait for the PENTAKILL 😱 {champ}",
            "why": f"This is why you never dive {champ} 😱 PENTAKILL",
        }[variant]
        what_happened = f"{champ} gets a 1v5 PENTAKILL in {match.queue_type}"
        streak_hashtag = "#Pentakill"
        streak_tag = "pentakill"
    elif highlight.kill_streak >= 2:
        streak = streak_label(highlight.kill_streak).upper()
        victims = _victims_phrase(highlight.victim_champions)
        emoji = _STREAK_EMOJI.get(highlight.kill_streak, "🔥")
        seed = f"{match.match_id}:{highlight.first_kill_ms or recorded_at.isoformat()}"
        variant = _pick_variant(seed)
        hype = _pick_hype(f"{match.match_id}:{highlight.kill_streak}")
        hook = {
            "hype": f"{champ} {hype} {streak} vs {victims} {emoji}",
            "wait": f"Wait for the {streak} {emoji} {champ} vs {victims}",
            "why": f"This is why you don't fight {champ} {emoji} {streak}",
        }[variant]
        what_happened = f"{champ} gets a {streak_label(highlight.kill_streak)} on {victims} in {match.queue_type}"
        streak_hashtag = f"#{streak_label(highlight.kill_streak).replace(' ', '')}"
        streak_tag = streak_label(highlight.kill_streak).lower()
    else:
        victim = display_name(highlight.victim_champions[0]) if highlight.victim_champions else "the enemy"
        seed = f"{match.match_id}:{victim}:{recorded_at.isoformat()}"
        variant = _pick_variant(seed)
        verb = _pick_verb(seed)
        hook = {
            "hype": f"{champ} {verb} {victim} 💀",
            "wait": f"Wait for it… {champ} vs {victim} 😳",
            "why": f"This is why you don't 1v1 {champ} 💀",
        }[variant]
        what_happened = f"{champ} {verb} {victim} in {match.queue_type}"
        streak_hashtag = None
        streak_tag = "solo kill"

    title_context = f" | {context_bits}" if context_bits else ""
    title = _fit_title(f"{hook}{title_context} #shorts")

    # 3-5 tightly-relevant hashtags outperform a long wall on Shorts. Lead with #Shorts (the
    # format signal), then the champion + streak (niche discovery), then broad reach.
    hashtags = ["#Shorts", f"#{champ_tag}"]
    if streak_hashtag:
        hashtags.append(streak_hashtag)
    hashtags += ["#LeagueOfLegends", "#LoL", "#Gaming"]

    rank_note = f" ({rank} Ranked)" if rank else ""
    patch_note = f", Patch {match.patch}" if match.patch else ""
    description = (
        f"{what_happened}{rank_note}{patch_note}. 🔥\n"
        f"{_cta(champ)}\n"
        f"{champ}{f' {match.role}' if match.role else ''} clips from my own games -- "
        f"full gameplay on the channel.\n\n" + " ".join(hashtags)
    )

    champ_lower = display_name(match.champion).lower()
    tags = _base_tags(match, rank) + [
        "league of legends shorts",
        "lol shorts",
        "lol clips",
        "league of legends clips",
        f"{champ_lower} clips",
        f"{champ_lower} montage",
        f"{champ_lower} best plays",
        "league of legends best plays",
        "lol montage",
        "league of legends 2026",
        "best league of legends plays",
    ]
    if streak_tag:
        tags.append(streak_tag)
        tags.append(f"{champ_lower} {streak_tag}")

    return DraftMetadata(
        title=title, description=description, hashtags=hashtags, tags=_cap_tags(tags), title_variant=variant
    )


def build_full_game_metadata(
    match: MatchContext, recorded_at: datetime, rank: str | None = None
) -> DraftMetadata:
    champ = display_name(match.champion)
    champ_tag = tag_name(match.champion)
    tier = _tier(rank)
    result = "WIN" if match.win else "LOSS"

    # Title principles (the old "Champ vs Champ Role - Platinum II Ranked Solo/Duo LOSS 4/8/12"
    # format sat at ~0 views): lead with what people actually search (the matchup + role),
    # keep the "Full Gameplay" search phrase, and only show the score when it's a brag --
    # a KDA hook on a win earns the click, "LOSS 4/8/12" repels it. Queue/result/KDA all
    # stay in the description for search; the title is for humans.
    if match.opponent_champion:
        matchup = f"{champ} vs {display_name(match.opponent_champion)}"
    else:
        matchup = champ
    role_bit = f" {match.role}" if match.role else ""
    # Only brag when there's something to brag about: a win with recorded kills. Some rows
    # (remakes, missing data) carry a 0/0/0 score -- "0/0/0 Illaoi vs Garen" repels harder
    # than no score at all.
    kda_hook = f"{match.kda} " if match.win and match.kills > 0 else ""
    tier_bit = f"{tier} " if tier else ""
    patch_bit = f" (Patch {match.patch})" if match.patch else ""
    title = _fit_title(f"{kda_hook}{matchup}{role_bit} — {tier_bit}Full Gameplay{patch_bit}")

    hashtags = ["#LeagueOfLegends", "#LoL", f"#{champ_tag}"]
    if match.opponent_champion:
        hashtags.append(f"#{tag_name(match.opponent_champion)}")
    if match.role:
        hashtags.append(f"#{match.role}")
    if tier:
        hashtags.append(f"#{tier}")
    hashtags += ["#Gaming", "#FullGameplay"]

    matchup_line = (
        f"{champ} vs {display_name(match.opponent_champion)} {match.role or ''} full game".strip()
        if match.opponent_champion
        else f"{champ} full game"
    )
    rank_note = f" at {rank}" if rank else ""
    patch_note = f" on Patch {match.patch}" if match.patch else ""
    description = (
        f"{matchup_line} in {match.queue_type}{rank_note}{patch_note}. "
        f"Result: {result}, KDA {match.kda}.\n"
        f"{_cta(champ)}\n"
        f"Unedited gameplay from my own games -- highlights on the channel.\n\n" + " ".join(hashtags)
    )

    tags = _base_tags(match, rank) + [
        "full gameplay",
        f"{display_name(match.champion).lower()} full game",
        "league of legends full game",
    ]
    if match.opponent_champion:
        opp = display_name(match.opponent_champion).lower()
        tags.append(f"{display_name(match.champion).lower()} vs {opp}")

    return DraftMetadata(title=title, description=description, hashtags=hashtags, tags=_cap_tags(tags))
