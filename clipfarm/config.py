"""Typed configuration: config.yaml (user-editable) overlaid with .env (secrets)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_YAML_PATH = PROJECT_ROOT / "config.yaml"


class AscentConfig(BaseModel):
    full_games_dir: Path
    clips_dir: Path
    ffmpeg_path: Path


class WatcherConfig(BaseModel):
    settle_check_interval_seconds: float = 3
    settle_required_stable_checks: int = 3


class RiotConfig(BaseModel):
    full_game_match_tolerance_minutes: int = 15
    full_game_max_duration_minutes: int = 60
    clip_roll_tolerance_seconds: int = 20
    multikill_cluster_seconds: int = 10
    # Unmatched items younger than this keep status 'awaiting_match' and are retried by the
    # background sweep (games in progress aren't in Match-V5 yet); older ones give up and go
    # 'ready' with generic metadata.
    rematch_give_up_hours: int = 48
    rematch_interval_seconds: int = 180


class ThumbnailConfig(BaseModel):
    clip_frame_offset_seconds: float = 1.5
    full_game_frame_offset_seconds: float = 60
    # Composite = gameplay frame + Data Dragon champion art + bold text. When off, falls back
    # to a plain frame-grab with a text overlay.
    composite_enabled: bool = True


class EditingConfig(BaseModel):
    # Clips get vertical conversion + music + loudness normalization + fades in one encode.
    enabled: bool = True
    music_dir: Path = Path("./music")
    # Music level under the game audio, 0.0-1.0. Game audio stays at full; music sits beneath.
    clip_music_volume: float = 0.18
    normalize_audio: bool = True
    # Fades default OFF for clips: the best-retention Shorts loop seamlessly (our own retention
    # leaders sit at 110-144% -- viewers rewatching), and a fade telegraphs the loop point.
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0
    # Small translucent channel handle burned bottom-center of every clip. Brand recall for
    # viewers who watch without ever opening the description; empty string disables it.
    watermark_text: str = ""
    # Burn a big hook caption ("PENTAKILL!", champion name, ...) over the first few seconds of
    # each Short. This is the single biggest retention lever on the Shorts feed: it tells a
    # muted, auto-scrolling viewer what they're about to see before they flick away.
    hook_caption: bool = True
    hook_seconds: float = 2.5
    # Flash a subscribe ask over the LAST seconds of each clip. Shorts viewers almost never
    # visit the channel page -- the ask has to happen inside the video, right after the payoff,
    # while the finger is hovering. Empty string disables it.
    subscribe_cta_text: str = ""
    subscribe_cta_seconds: float = 2.5
    # Flash a brief like ask mid-clip (near the action's peak). A like is the one ranking
    # signal a viewer can give without leaving the video; nobody taps unless asked. Empty
    # string disables it. NOTE: the burned font has no emoji glyphs -- use words, not 👍.
    like_cta_text: str = ""
    like_cta_seconds: float = 2.0


class YoutubeConfig(BaseModel):
    default_full_game_visibility: str = "unlisted"
    default_clip_visibility: str = "unlisted"
    daily_upload_quota: int = 100
    # Posted as the channel's own first comment under every upload -- a question invites
    # replies, and comment activity is a ranking signal we can seed ourselves. Empty disables.
    # Needs the comments scope: run `reauth-youtube` once after enabling.
    auto_comment_text: str = ""
    # Optional rotation pool. When non-empty it replaces auto_comment_text: each upload picks
    # one (deterministically, by video id), so the channel isn't posting the exact same
    # sentence several times a day -- identical repeated comments read as spam to both
    # viewers and YouTube's filter.
    auto_comment_texts: list[str] = []


class FacebookConfig(BaseModel):
    enabled: bool = False


class TiktokConfig(BaseModel):
    enabled: bool = False
    force_self_only: bool = True


class DatabaseConfig(BaseModel):
    path: Path


class LoggingConfig(BaseModel):
    path: Path
    level: str = "INFO"


class Secrets(BaseSettings):
    """Loaded from .env / real environment variables."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    riot_api_key: str
    riot_region: str = "americas"
    riot_platform: str = "na1"
    riot_game_name: str
    riot_tag_line: str

    google_client_secrets_path: Path | None = None

    fb_page_id: str | None = None
    fb_page_access_token: str | None = None
    # App ID/secret are only needed by `reauth-facebook` to mint a durable Page token with
    # read_insights (the analytics equivalent of the YouTube analytics scope). Optional otherwise.
    fb_app_id: str | None = None
    fb_app_secret: str | None = None

    tiktok_client_key: str | None = None
    tiktok_client_secret: str | None = None


class Settings(BaseModel):
    ascent: AscentConfig
    watcher: WatcherConfig
    riot: RiotConfig
    thumbnails: ThumbnailConfig
    editing: EditingConfig
    youtube: YoutubeConfig
    facebook: FacebookConfig
    tiktok: TiktokConfig
    database: DatabaseConfig
    logging: LoggingConfig
    secrets: Secrets

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


def _load_yaml() -> dict:
    if not CONFIG_YAML_PATH.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {CONFIG_YAML_PATH}. Copy the example and edit it."
        )
    with CONFIG_YAML_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve(path: Path) -> Path:
    """`~` expands to the user's home folder (so config.yaml carries no machine-specific
    username), and relative paths are anchored to the project root, not the process's cwd,
    so `clipfarm.cli` behaves the same regardless of where it's launched from."""
    path = path.expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@lru_cache
def get_settings() -> Settings:
    raw = _load_yaml()
    database = DatabaseConfig(**raw["database"])
    database.path = _resolve(database.path)
    logging_cfg = LoggingConfig(**raw["logging"])
    logging_cfg.path = _resolve(logging_cfg.path)
    editing = EditingConfig(**raw.get("editing", {}))
    editing.music_dir = _resolve(editing.music_dir)
    ascent = AscentConfig(**raw["ascent"])
    ascent.full_games_dir = _resolve(ascent.full_games_dir)
    ascent.clips_dir = _resolve(ascent.clips_dir)
    ascent.ffmpeg_path = _resolve(ascent.ffmpeg_path)

    return Settings(
        ascent=ascent,
        watcher=WatcherConfig(**raw.get("watcher", {})),
        riot=RiotConfig(**raw.get("riot", {})),
        thumbnails=ThumbnailConfig(**raw.get("thumbnails", {})),
        editing=editing,
        youtube=YoutubeConfig(**raw.get("youtube", {})),
        facebook=FacebookConfig(**raw.get("facebook", {})),
        tiktok=TiktokConfig(**raw.get("tiktok", {})),
        database=database,
        logging=logging_cfg,
        secrets=Secrets(),  # type: ignore[call-arg]
    )
