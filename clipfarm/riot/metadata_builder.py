"""Turn match/timeline data into draft titles, descriptions, hashtags, and YouTube tags.

Everything here is a *draft* -- the dashboard allows editing before anything posts.

What the drafts optimise for (and what they deliberately avoid):
- **Read like a person typed them.** No "#shorts" bolted onto every title (YouTube detects the
  format from the video itself), no em dashes, no wall of emoji, no shouting HYPE WORDS. One
  emoji at most, and only on the direct-hype variant. A feed full of identical templated titles
  is the quickest way to read as a clip farm, which both viewers and the ranking system punish.
- **Search phrases first.** Champion name leads, the matchup ("Draven vs Jinx") is spelled out
  wherever we know it, and the rank/role phrase people actually type ("Emerald Jungle") rides
  along when it fits. Full games keep "Full Gameplay" because that is the literal query.
- **Kill-clip titles rotate through three hook styles** ("hype" / "wait" / "why"), picked
  deterministically per clip (seeded by match id + kill timestamp) so a batch of uploads
  doesn't all look the same, regeneration is stable, and -- because the chosen style is stored
  on the row -- the metrics dashboard can measure which style earns the most views.
- **Descriptions front-load the searchable sentence** (what YouTube weighs most), then one
  plain subscribe line, then a small hashtag block. The first three hashtags show above the
  title on YouTube, so those are the champion and the game, not "#Shorts".
- A separate `tags` list feeds YouTube's tags field (500-char budget) with the long-tail and
  matchup phrases that don't belong in visible hashtags.
"""
from __future__ import annotations

import zlib
from datetime import datetime

from clipfarm.jobs.models import ClipHighlight, DraftMetadata, MatchContext
from clipfarm.riot.champion_names import display_name, tag_name

KILL_STREAK_NAMES = {1: "Kill", 2: "Double Kill", 3: "Triple Kill", 4: "Quadra Kill", 5: "Penta Kill"}

_SINGLE_KILL_VERBS = ["outplays", "picks off", "deletes", "one-shots", "punishes", "catches out", "runs down"]

# A single emoji on the direct-hype variant only. Real LoL clip titles use one or none; a row
# of them is the tell of a generated title.
_STREAK_EMOJI = {2: "⚔️", 3: "🔥", 4: "😳", 5: "😱"}

YOUTUBE_TITLE_LIMIT = 100
# The Shorts feed shows roughly two lines of title; past this the rank/role suffix is dropped
# rather than letting the hook get cut off. (The hard limit above is still enforced.)
SHORTS_TITLE_TARGET = 80
YOUTUBE_TAGS_CHAR_BUDGET = 480  # stay under YouTube's 500-char tags limit with margin


def _cta(champ: str) -> str:
    """One quiet subscribe line. It is the same ask every time (consistency trains the
    audience) but phrased like a creator, not a banner."""
    return f"Subscribe for more {champ}."


def streak_label(streak: int) -> str:
    return KILL_STREAK_NAMES.get(streak, f"{streak}-Kill Streak")


def _fit_title(title: str) -> str:
    if len(title) <= YOUTUBE_TITLE_LIMIT:
        return title
    return title[: YOUTUBE_TITLE_LIMIT - 1].rsplit(" ", 1)[0] + "…"


def _pick_verb(seed: str) -> str:
    return _SINGLE_KILL_VERBS[zlib.crc32(seed.encode()) % len(_SINGLE_KILL_VERBS)]


# The A/B title experiment. Each kill clip is assigned one of three hook styles; the choice is
# stored on the row (media_files.title_variant) so the metrics dashboard can compare avg views
# and retention per style and we learn which hook actually stops the scroll:
#   hype -- direct statement:        "Draven Triple Kill vs Jinx & Lulu 🔥 | Emerald ADC"
#   wait -- curiosity gap / tease:   "Wait for the triple kill… Draven vs Jinx & Lulu"
#   why  -- lesson / matchup frame:  "This is why you don't fight Draven in Emerald"
_TITLE_VARIANTS = ("hype", "wait", "why")


def _pick_variant(seed: str) -> str:
    # Salted so the variant choice is independent of the verb pick on the same seed.
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


def _with_context(hook: str, context: str) -> str:
    """Append the ' | Emerald Jungle' search phrase when the title stays short enough to read
    in the Shorts feed; otherwise the hook stands alone (the description still carries it)."""
    if not context:
        return _fit_title(hook)
    full = f"{hook} | {context}"
    return _fit_title(full if len(full) <= SHORTS_TITLE_TARGET else hook)


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


def _clip_hashtags(champ_tag: str, streak_hashtag: str | None) -> list[str]:
    """Four or five tight hashtags. YouTube surfaces the first three above the title, so lead
    with the champion and the game (what a searcher recognises), keep the streak for niche
    discovery, and put the format signal last -- it still counts, it just isn't a headline."""
    tags = [f"#{champ_tag}", "#LeagueOfLegends"]
    if streak_hashtag:
        tags.append(streak_hashtag)
    tags += ["#LoLClips", "#Shorts"]
    return tags


def build_clip_metadata(
    match: MatchContext | None,
    highlight: ClipHighlight | None,
    recorded_at: datetime,
    rank: str | None = None,
) -> DraftMetadata:
    if match is None:
        return DraftMetadata(
            title=f"League of Legends highlight ({recorded_at:%b %d})",
            description=(
                "A highlight from one of my own ranked games.\n"
                f"{_cta('League of Legends')}\n\n"
                "#LeagueOfLegends #LoLClips #Shorts"
            ),
            hashtags=["#LeagueOfLegends", "#LoLClips", "#Shorts"],
            tags=["league of legends", "lol", "league of legends shorts", "lol clips",
                  "league of legends best plays", "lol montage"],
        )

    champ = display_name(match.champion)
    champ_tag = tag_name(match.champion)
    tier = _tier(rank)
    context = " ".join(bit for bit in [tier, match.role] if bit)  # e.g. "Emerald Jungle"
    in_tier = f" in {tier}" if tier else ""

    variant: str | None = None
    matchup_tags: list[str] = []
    if highlight is None:
        role_bit = f" {match.role}" if match.role else ""
        title = _with_context(f"{champ}{role_bit} highlight", tier or "")
        what_happened = f"{champ} highlight from a {match.queue_type} game"
        streak_hashtag = None
        streak_tag = None
    elif highlight.kill_streak >= 5:
        seed = f"{match.match_id}:{highlight.first_kill_ms or recorded_at.isoformat()}"
        variant = _pick_variant(seed)
        title = {
            "hype": _with_context(f"{champ} PENTAKILL 😱", context),
            "wait": _with_context(f"Wait for the PENTAKILL… {champ}", context),
            "why": _fit_title(f"This is why you don't dive {champ}{in_tier} (PENTAKILL)"),
        }[variant]
        what_happened = f"{champ} pentakill in {match.queue_type}"
        streak_hashtag = "#Pentakill"
        streak_tag = "pentakill"
    elif highlight.kill_streak >= 2:
        streak = streak_label(highlight.kill_streak)
        victims = _victims_phrase(highlight.victim_champions)
        emoji = _STREAK_EMOJI.get(highlight.kill_streak, "🔥")
        seed = f"{match.match_id}:{highlight.first_kill_ms or recorded_at.isoformat()}"
        variant = _pick_variant(seed)
        title = {
            "hype": _with_context(f"{champ} {streak} vs {victims} {emoji}", context),
            "wait": _with_context(f"Wait for the {streak.lower()}… {champ} vs {victims}", context),
            "why": _fit_title(f"This is why you don't fight {champ}{in_tier} ({streak})"),
        }[variant]
        what_happened = f"{champ} {streak.lower()} on {victims} in {match.queue_type}"
        streak_hashtag = f"#{streak.replace(' ', '')}"
        streak_tag = streak.lower()
        if highlight.victim_champions:
            matchup_tags.append(f"{champ.lower()} vs {display_name(highlight.victim_champions[0]).lower()}")
    else:
        victim = display_name(highlight.victim_champions[0]) if highlight.victim_champions else "the enemy"
        seed = f"{match.match_id}:{victim}:{recorded_at.isoformat()}"
        variant = _pick_variant(seed)
        verb = _pick_verb(seed)
        title = {
            "hype": _with_context(f"{champ} {verb} {victim} 💀", context),
            "wait": _with_context(f"Wait for it… {champ} vs {victim}", context),
            # The matchup phrase is what people search ("draven vs jinx"); frame it as a lesson.
            "why": _with_context(f"How to punish {victim} as {champ}", context),
        }[variant]
        what_happened = f"{champ} {verb} {victim} in {match.queue_type}"
        streak_hashtag = None
        streak_tag = "solo kill"
        if highlight.victim_champions:
            matchup_tags.append(f"{champ.lower()} vs {victim.lower()}")

    hashtags = _clip_hashtags(champ_tag, streak_hashtag)

    detail_bits = [bit for bit in [rank, f"patch {match.patch}" if match.patch else None] if bit]
    details = f" ({', '.join(detail_bits)})" if detail_bits else ""
    role_bit = f" {match.role}" if match.role else ""
    description = (
        f"{what_happened}{details}.\n"
        f"{champ}{role_bit} clips from my own ranked games, full games are on the channel too.\n"
        f"{_cta(champ)}\n\n" + " ".join(hashtags)
    )

    champ_lower = champ.lower()
    tags = _base_tags(match, rank) + matchup_tags + [
        "league of legends shorts",
        "lol shorts",
        "lol clips",
        "league of legends clips",
        f"{champ_lower} clips",
        f"{champ_lower} montage",
        f"{champ_lower} best plays",
        "league of legends best plays",
        "lol montage",
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

    # Lead with what people actually search (the matchup + role), keep the "Full Gameplay"
    # search phrase, and only show the score when it's a brag -- a KDA hook on a win earns the
    # click, "LOSS 4/8/12" repels it. Queue/result/KDA all stay in the description for search.
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
    title = _fit_title(f"{kda_hook}{matchup}{role_bit} | {tier_bit}Full Gameplay{patch_bit}")

    hashtags = ["#LeagueOfLegends", f"#{champ_tag}"]
    if match.opponent_champion:
        hashtags.append(f"#{tag_name(match.opponent_champion)}")
    if match.role:
        hashtags.append(f"#{match.role}")
    if tier:
        hashtags.append(f"#{tier}")
    hashtags.append("#FullGameplay")

    matchup_line = (
        f"{champ} vs {display_name(match.opponent_champion)} {match.role or ''} full game".strip()
        if match.opponent_champion
        else f"{champ} full game"
    )
    rank_note = f" at {rank}" if rank else ""
    patch_note = f" on patch {match.patch}" if match.patch else ""
    description = (
        f"{matchup_line} in {match.queue_type}{rank_note}{patch_note}. "
        f"Result: {result}, KDA {match.kda}.\n"
        f"Unedited from my own games. Highlights from this game and more {champ} are on the channel.\n"
        f"{_cta(champ)}\n\n" + " ".join(hashtags)
    )

    tags = _base_tags(match, rank) + [
        "full gameplay",
        f"{champ.lower()} full game",
        "league of legends full game",
    ]
    if match.opponent_champion:
        opp = display_name(match.opponent_champion).lower()
        tags.append(f"{champ.lower()} vs {opp}")
        tags.append(f"{champ.lower()} vs {opp} {match.role.lower()}" if match.role else f"{opp} matchup")

    return DraftMetadata(title=title, description=description, hashtags=hashtags, tags=_cap_tags(tags))
