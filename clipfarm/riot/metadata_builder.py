"""Turn match/timeline data into draft titles, descriptions, hashtags, and YouTube tags.

Everything here is a *draft* -- the dashboard allows editing before anything posts.

What the drafts optimise for (and what they deliberately avoid):
- **Read like a person typed them.** No "#shorts" bolted onto every title (YouTube detects the
  format from the video itself), no em dashes, no wall of emoji, no shouting HYPE WORDS. One
  emoji at most, and only sometimes. A feed full of identical templated titles is the quickest
  way to read as a clip farm, which both viewers and the ranking system punish.
- **Variety, deliberately.** Each kill clip draws its title from a pool of dozens of sentence
  shapes (see `_SOLO`, `_MULTI`, `_PENTA`), grouped into four hook *families* -- "hype",
  "wait", "why", "question" -- with the suffix style, verb, adjective, and emoji all rolled
  separately. Two clips of the same champion posted back to back therefore read as different
  posts, not one template with the victim swapped. The scheduler leans on this too: a draft
  carries one alternate title per family (`title_alternates`) so it can swap shapes when the
  previous post already used the same one (see jobs/scheduler.py).
- **Search phrases first.** Champion name leads most shapes, the matchup ("Draven vs Jinx") is
  spelled out wherever we know it, and the rank/role phrase people actually type ("Emerald
  Jungle") rides along when it fits. Full games keep "Full Gameplay" because that is the query.
- **Deterministic.** Every roll is seeded by match id + kill timestamp, so regenerating a draft
  gives the same title, and the chosen family is stored on the row (`title_variant`) so the
  metrics dashboard can measure which family earns the most views.
- **Descriptions front-load the searchable sentence** (what YouTube weighs most), then a
  plain subscribe line, then a small hashtag block. The first three hashtags show above the
  title on YouTube, so those are the champion and the game, not "#Shorts".
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime

from clipfarm.jobs.models import ClipHighlight, DraftMetadata, MatchContext
from clipfarm.riot.champion_names import display_name, tag_name

KILL_STREAK_NAMES = {1: "Kill", 2: "Double Kill", 3: "Triple Kill", 4: "Quadra Kill", 5: "Penta Kill"}

_SINGLE_KILL_VERBS = ["outplays", "picks off", "deletes", "one-shots", "punishes", "catches out", "runs down"]
# Mild, specific adjectives. "INSANE/DISGUSTING" on every title is the clip-farm tell.
_ADJECTIVES = ["Clean", "Free", "Textbook", "Easy", "Ruthless", "Quick"]
# A single emoji, only on shapes that opt in, and only half the time.
_STREAK_EMOJI = {1: "💀", 2: "⚔️", 3: "🔥", 4: "😳", 5: "😱"}

YOUTUBE_TITLE_LIMIT = 100
# The Shorts feed shows roughly two lines of title; past this the rank/role suffix is dropped
# rather than letting the hook get cut off. (The hard limit above is still enforced.)
SHORTS_TITLE_TARGET = 80
YOUTUBE_TAGS_CHAR_BUDGET = 480  # stay under YouTube's 500-char tags limit with margin

# The hook families. Stored per row as `title_variant` so the metrics page can score them.
TITLE_FAMILIES = ("hype", "wait", "why", "question")


def streak_label(streak: int) -> str:
    return KILL_STREAK_NAMES.get(streak, f"{streak}-Kill Streak")


def _roll(seed: str, salt: str, n: int) -> int:
    """Deterministic pick in range(n). Each salt is an independent roll off the same seed, so
    the template, suffix style, verb, and emoji choices don't move in lockstep."""
    return zlib.crc32(f"{salt}:{seed}".encode()) % n if n else 0


def _fit_title(title: str) -> str:
    if len(title) <= YOUTUBE_TITLE_LIMIT:
        return title
    return title[: YOUTUBE_TITLE_LIMIT - 1].rsplit(" ", 1)[0] + "…"


# --- the title pools ------------------------------------------------------------------------

@dataclass(frozen=True)
class _Shape:
    family: str
    text: str                        # format string over the placeholders in _placeholders()
    needs: tuple[str, ...] = ()      # placeholders that must be non-empty for this shape
    suffix: bool = True              # may take a " | Emerald ADC" style context suffix
    emoji: bool = False              # may take the streak emoji at the end


_SOLO: tuple[_Shape, ...] = (
    # hype -- direct statement of what happened
    _Shape("hype", "{champ} {verb} {victim}", needs=("victim",), emoji=True),
    _Shape("hype", "{adj} {champ} kill on {victim}", needs=("victim",)),
    _Shape("hype", "{champ} {verb} {victim} in {tier} {role}", needs=("victim", "tier", "role"), suffix=False),
    _Shape("hype", "{victim} did not see that {champ} coming", needs=("victim",)),
    _Shape("hype", "{champ} vs {victim} and it's not close", needs=("victim",)),
    _Shape("hype", "{champ} solo kill in {tier}", needs=("tier",), suffix=False, emoji=True),
    _Shape("hype", "{adj} {champ} pick in {queue}", suffix=False),
    # wait -- curiosity gap, payoff tease
    _Shape("wait", "Wait for it… {champ} vs {victim}", needs=("victim",)),
    _Shape("wait", "{victim} thought they were safe", needs=("victim",)),
    _Shape("wait", "Watch what {champ} does to {victim}", needs=("victim",)),
    _Shape("wait", "Nobody expected the {champ} play", emoji=True),
    _Shape("wait", "{victim} walked up to {champ} one time too many", needs=("victim",)),
    _Shape("wait", "Keep watching the {champ}"),
    # why -- lesson / matchup framing (the literal searches people make)
    _Shape("why", "How to punish {victim} as {champ}", needs=("victim",)),
    _Shape("why", "This is why you don't 1v1 {champ}"),
    _Shape("why", "{champ} vs {victim} matchup in one clip", needs=("victim",)),
    _Shape("why", "How {champ} wins the {victim} matchup", needs=("victim",)),
    _Shape("why", "Don't walk up to {champ} as {victim}", needs=("victim",)),
    _Shape("why", "This is what {champ} does to {victim} in {tier}", needs=("victim", "tier"), suffix=False),
    _Shape("why", "{champ} {role} tip: punish {victim} like this", needs=("victim", "role"), suffix=False),
    # question -- invites a comment, which is the engagement signal we can't fake
    _Shape("question", "Was this {champ} play clean or lucky?"),
    _Shape("question", "How did {victim} not see that {champ} coming?", needs=("victim",)),
    _Shape("question", "Is {champ} broken in {tier}?", needs=("tier",), suffix=False),
    _Shape("question", "Rate this {champ} outplay on {victim}", needs=("victim",)),
    _Shape("question", "Should {victim} have won this trade vs {champ}?", needs=("victim",)),
    _Shape("question", "Would you have flashed here as {victim}?", needs=("victim",)),
)

_MULTI: tuple[_Shape, ...] = (
    # hype
    _Shape("hype", "{champ} {streak} vs {victims}", needs=("victims",), emoji=True),
    _Shape("hype", "{adj} {streak_lower} as {champ}"),
    _Shape("hype", "{champ} {streak_lower} in {tier} {role}", needs=("tier", "role"), suffix=False, emoji=True),
    _Shape("hype", "{victims} all fell to {champ}", needs=("victims",)),
    _Shape("hype", "{n} kills in {seconds} seconds as {champ}", needs=("seconds",), emoji=True),
    _Shape("hype", "{champ} cleans up {victims}", needs=("victims",)),
    _Shape("hype", "{streak} for {champ} in {queue}", suffix=False),
    # wait
    _Shape("wait", "Wait for the {streak_lower}… {champ} vs {victims}", needs=("victims",)),
    _Shape("wait", "{champ} wasn't done after the first kill"),
    _Shape("wait", "They should have backed off from {champ}"),
    _Shape("wait", "Watch {champ} chain the {streak_lower}", emoji=True),
    _Shape("wait", "{victims} thought this fight was over", needs=("victims",)),
    _Shape("wait", "One more… {champ} {streak_lower}"),
    # why
    _Shape("why", "This is why you don't fight {champ} in {tier}", needs=("tier",), suffix=False),
    _Shape("why", "This is why you don't fight {champ} ({streak})", suffix=False),
    _Shape("why", "How to get a {streak_lower} as {champ}"),
    _Shape("why", "Why {champ} is scary in {tier} {role}", needs=("tier", "role"), suffix=False),
    _Shape("why", "How {champ} turns one kill into a {streak_lower}"),
    _Shape("why", "{champ} vs {victims}: how the {streak_lower} happens", needs=("victims",), suffix=False),
    # question
    _Shape("question", "Was this {champ} {streak_lower} earned or gifted?"),
    _Shape("question", "Who saw the {streak_lower} coming? {champ} vs {victims}", needs=("victims",)),
    _Shape("question", "Is this the cleanest {champ} {streak_lower} you've seen?"),
    _Shape("question", "{streak} as {champ}, rate it 1-10"),
    _Shape("question", "What should {victims} have done vs {champ}?", needs=("victims",)),
)

_PENTA: tuple[_Shape, ...] = (
    _Shape("hype", "{champ} PENTAKILL", emoji=True),
    _Shape("hype", "{champ} PENTAKILL in {tier} {role}", needs=("tier", "role"), suffix=False, emoji=True),
    _Shape("hype", "PENTAKILL as {champ} vs {victims}", needs=("victims",)),
    _Shape("hype", "{adj} {champ} PENTAKILL in {queue}", suffix=False),
    _Shape("wait", "Wait for the PENTAKILL… {champ}"),
    _Shape("wait", "{champ} had no business getting this penta"),
    _Shape("wait", "Watch {champ} chain the PENTAKILL", emoji=True),
    _Shape("wait", "They kept walking into {champ}. All five."),
    _Shape("why", "This is why you don't dive {champ} in {tier}", needs=("tier",), suffix=False),
    _Shape("why", "How to get a PENTAKILL as {champ}"),
    _Shape("why", "Why a fed {champ} is unplayable"),
    _Shape("why", "{champ} PENTAKILL and how it happened", suffix=False),
    _Shape("question", "Is this the cleanest {champ} PENTAKILL you've seen?"),
    _Shape("question", "Rate this {champ} PENTAKILL 1-10"),
    _Shape("question", "Who gave {champ} this penta? {victims}", needs=("victims",)),
)

_HIGHLIGHT: tuple[_Shape, ...] = (
    _Shape("hype", "{champ} {role} highlight", needs=("role",)),
    _Shape("hype", "{champ} play from a {tier} game", needs=("tier",), suffix=False),
    _Shape("hype", "{champ} vs {opp} highlight", needs=("opp",)),
    _Shape("wait", "Watch the {champ}"),
    _Shape("why", "How {champ} plays the {opp} matchup", needs=("opp",)),
    _Shape("question", "Rate this {champ} play"),
)

_POOLS = {"solo": _SOLO, "multi": _MULTI, "penta": _PENTA, "highlight": _HIGHLIGHT}


def _victims_phrase(victims: list[str]) -> str:
    names = [display_name(v) for v in victims]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    if len(names) == 3:
        return f"{names[0]}, {names[1]} & {names[2]}"
    return f"{names[0]}, {names[1]} & more"


def _tier(rank: str | None) -> str | None:
    """'Emerald II' -> 'Emerald'."""
    return rank.split()[0] if rank else None


def _placeholders(match: MatchContext, highlight: ClipHighlight | None, rank: str | None, seed: str) -> dict:
    victims = list(highlight.victim_champions) if highlight else []
    streak = highlight.kill_streak if highlight else 0
    seconds = None
    if highlight and highlight.first_kill_ms is not None and highlight.last_kill_ms is not None and streak >= 2:
        seconds = max(1, round((highlight.last_kill_ms - highlight.first_kill_ms) / 1000))
    return {
        "champ": display_name(match.champion),
        "victim": display_name(victims[0]) if victims else "",
        "victims": _victims_phrase(victims) if victims else "",
        "streak": streak_label(streak) if streak >= 2 else "",
        "streak_lower": streak_label(streak).lower() if streak >= 2 else "",
        "n": str(streak) if streak >= 2 else "",
        "seconds": str(seconds) if seconds else "",
        "tier": _tier(rank) or "",
        "role": match.role or "",
        "queue": match.queue_type,
        "opp": display_name(match.opponent_champion) if match.opponent_champion else "",
        "adj": _ADJECTIVES[_roll(seed, "adj", len(_ADJECTIVES))],
        "verb": _SINGLE_KILL_VERBS[_roll(seed, "verb", len(_SINGLE_KILL_VERBS))],
    }


def _apply_suffix(hook: str, shape: _Shape, ctx: dict, seed: str) -> str:
    """Attach the rank/role search phrase in one of several styles (or none), only while the
    title stays short enough to read in the Shorts feed; the description carries it anyway."""
    context = " ".join(bit for bit in [ctx["tier"], ctx["role"]] if bit)
    if not (shape.suffix and context):
        return _fit_title(hook)
    style = _roll(seed, "suffix", 4)
    candidates = [f"{hook} | {context}", f"{hook} ({context})", f"{hook} | {context} clip", hook]
    full = candidates[style]
    return _fit_title(full if len(full) <= SHORTS_TITLE_TARGET else hook)


def _render(shape: _Shape, ctx: dict, seed: str, streak: int) -> str:
    hook = shape.text.format(**ctx)
    if shape.emoji and _roll(seed, "emoji", 2) == 0:
        hook = f"{hook} {_STREAK_EMOJI.get(streak, '🔥')}"
    return _apply_suffix(hook, shape, ctx, seed)


def _eligible(pool: tuple[_Shape, ...], ctx: dict) -> list[_Shape]:
    return [s for s in pool if all(ctx.get(k) for k in s.needs)]


def _pick_titles(pool_name: str, ctx: dict, seed: str, streak: int) -> tuple[str, str, dict[str, str]]:
    """(title, family, alternates). The title is one seeded shape from the whole pool; the
    alternates hold one seeded shape per family so a caller can switch shape without
    re-deriving anything (see scheduler)."""
    shapes = _eligible(_POOLS[pool_name], ctx)
    chosen = shapes[_roll(seed, "shape", len(shapes))]
    alternates: dict[str, str] = {}
    for family in TITLE_FAMILIES:
        family_shapes = [s for s in shapes if s.family == family]
        if not family_shapes:
            continue
        shape = chosen if chosen.family == family else family_shapes[_roll(seed, f"alt:{family}", len(family_shapes))]
        alternates[family] = _render(shape, ctx, seed, streak)
    return alternates[chosen.family], chosen.family, alternates


# --- descriptions / tags --------------------------------------------------------------------

def _cta(champ: str, seed: str) -> str:
    """One quiet subscribe line, worded a few ways so a run of posts doesn't end identically.
    Always contains the word "subscribe" (publishers/facebook.py swaps it for "follow")."""
    options = [
        f"Subscribe for more {champ}.",
        f"More {champ} on the channel, subscribe if you want it.",
        f"Subscribe if you want to see more {champ} like this.",
        f"Subscribe, there's a lot more {champ} where this came from.",
    ]
    return options[_roll(seed, "cta", len(options))]


def _about_line(champ: str, role: str | None, tier: str | None, queue: str, seed: str) -> str:
    role_bit = f" {role}" if role else ""
    options = [
        f"{champ}{role_bit} clips from my own ranked games, full games are on the channel too.",
        f"Clipped from my own {queue} games. Full VODs are on the channel.",
        "All clips are from my own games, no reuploads.",
    ]
    if tier:
        options.append(f"Playing {champ}{role_bit} in {tier} this season, posting the good moments here.")
    return options[_roll(seed, "about", len(options))]


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
                "Subscribe for more League of Legends.\n\n"
                "#LeagueOfLegends #LoLClips #Shorts"
            ),
            hashtags=["#LeagueOfLegends", "#LoLClips", "#Shorts"],
            tags=["league of legends", "lol", "league of legends shorts", "lol clips",
                  "league of legends best plays", "lol montage"],
        )

    champ = display_name(match.champion)
    champ_tag = tag_name(match.champion)
    tier = _tier(rank)
    streak = highlight.kill_streak if highlight else 0
    kill_ms = highlight.first_kill_ms if highlight and highlight.first_kill_ms is not None else None
    seed = f"{match.match_id}:{kill_ms if kill_ms is not None else recorded_at.isoformat()}"
    ctx = _placeholders(match, highlight, rank, seed)

    variant: str | None = None
    alternates: dict[str, str] = {}
    matchup_tags: list[str] = []
    if highlight is None:
        title, _, _ = _pick_titles("highlight", ctx, seed, 0)
        what_happened = f"{champ} highlight from a {match.queue_type} game"
        streak_hashtag = None
        streak_tag = None
    elif streak >= 5:
        title, variant, alternates = _pick_titles("penta", ctx, seed, streak)
        what_happened = f"{champ} pentakill in {match.queue_type}"
        streak_hashtag = "#Pentakill"
        streak_tag = "pentakill"
    elif streak >= 2:
        title, variant, alternates = _pick_titles("multi", ctx, seed, streak)
        label = streak_label(streak)
        what_happened = f"{champ} {label.lower()} on {ctx['victims'] or 'the enemy team'} in {match.queue_type}"
        streak_hashtag = f"#{label.replace(' ', '')}"
        streak_tag = label.lower()
        if highlight.victim_champions:
            matchup_tags.append(f"{champ.lower()} vs {display_name(highlight.victim_champions[0]).lower()}")
    else:
        title, variant, alternates = _pick_titles("solo", ctx, seed, 1)
        victim = ctx["victim"] or "the enemy"
        what_happened = f"{champ} {ctx['verb']} {victim} in {match.queue_type}"
        streak_hashtag = None
        streak_tag = "solo kill"
        if highlight.victim_champions:
            matchup_tags.append(f"{champ.lower()} vs {victim.lower()}")

    hashtags = _clip_hashtags(champ_tag, streak_hashtag)

    detail_bits = [bit for bit in [rank, f"patch {match.patch}" if match.patch else None] if bit]
    details = f" ({', '.join(detail_bits)})" if detail_bits else ""
    description = (
        f"{what_happened}{details}.\n"
        f"{_about_line(champ, match.role, tier, match.queue_type, seed)}\n"
        f"{_cta(champ, seed)}\n\n" + " ".join(hashtags)
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
        title=title, description=description, hashtags=hashtags, tags=_cap_tags(tags),
        title_variant=variant, title_alternates=alternates,
    )


def build_full_game_metadata(
    match: MatchContext, recorded_at: datetime, rank: str | None = None
) -> DraftMetadata:
    champ = display_name(match.champion)
    champ_tag = tag_name(match.champion)
    tier = _tier(rank)
    result = "WIN" if match.win else "LOSS"
    seed = f"{match.match_id}:full"

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
        f"{_cta(champ, seed)}\n\n" + " ".join(hashtags)
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
