"""YouTube uploads via OAuth installed-app flow + resumable videos.insert."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from clipfarm.config import get_settings
from clipfarm.media.edit import hook_text_for, prepared_clip
from clipfarm.publishers import quota

logger = logging.getLogger(__name__)

# youtube = upload/manage; yt-analytics.readonly = watch time / CTR / impressions for the metrics
# dashboard. Adding the analytics scope means the next `reauth-youtube` requests it; the existing
# token keeps working for uploads and basic stats until then.
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class YouTubeAuthError(RuntimeError):
    """No cached/valid credentials -- the user needs to run the one-time OAuth consent."""


class YouTubeQuotaExceededError(RuntimeError):
    """The local daily upload quota tracker says we've hit today's dedicated upload bucket."""


class YouTubeUploadError(RuntimeError):
    """Upload rejected by YouTube, translated into an actionable message."""


# Google error `reason` -> what the user should actually do about it.
_REASON_MESSAGES = {
    "uploadLimitExceeded": (
        "YouTube says this channel hit its daily upload limit (a channel-level cap, separate "
        "from API quota -- newer/unverified channels get low limits). It resets within ~24h; "
        "verifying the channel at youtube.com/verify raises it. Retry this item tomorrow."
    ),
    "quotaExceeded": "YouTube API quota exhausted for today -- retry after midnight Pacific.",
    "dailyLimitExceeded": "YouTube API quota exhausted for today -- retry after midnight Pacific.",
    "forbidden": "YouTube refused the upload (forbidden) -- check the channel is in good standing.",
}


def _friendly_upload_error(exc: HttpError) -> YouTubeUploadError:
    reason = None
    try:
        details = json.loads(exc.content.decode())["error"]["errors"][0]
        reason = details.get("reason")
        message = details.get("message", "")
    except Exception:  # noqa: BLE001 -- fall back to the raw error text
        message = str(exc)
    return YouTubeUploadError(_REASON_MESSAGES.get(reason, f"YouTube upload failed: {message}"))


def _sanitize(text: str) -> str:
    """YouTube rejects angle brackets in titles/descriptions."""
    return text.replace("<", "(").replace(">", ")")


def _token_path() -> Path:
    return get_settings().project_root / "data" / "youtube_token.json"


def _load_credentials() -> Credentials:
    token_path = _token_path()
    if not token_path.exists():
        raise YouTubeAuthError(
            "No cached YouTube credentials -- run `python -m clipfarm.cli reauth-youtube` once "
            "to authorize (requires GOOGLE_CLIENT_SECRETS_PATH in .env to point at a downloaded "
            "OAuth Client ID JSON from Google Cloud Console)."
        )
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
        except RefreshError as exc:
            raise YouTubeAuthError(
                "YouTube authorization was revoked or expired -- run "
                "`python -m clipfarm.cli reauth-youtube` to sign in again."
            ) from exc
        token_path.write_text(creds.to_json())
    return creds


def _build_service():
    return build("youtube", "v3", credentials=_load_credentials())




def delete(video_id: str) -> None:
    """Deletes a previously-uploaded video. YouTube's API has no way to replace a video's
    file content in place -- this is only useful paired with a fresh publish() call."""
    service = _build_service()
    service.videos().delete(id=video_id).execute()


def publish(media_file: sqlite3.Row) -> str:
    """Uploads a full game (as-is) or a clip (converted to vertical, tagged as a Short).
    Returns the new YouTube video id. Raises on quota exhaustion or upload failure."""
    settings = get_settings()

    if not quota.check("youtube", settings.youtube.daily_upload_quota):
        raise YouTubeQuotaExceededError(
            f"YouTube daily upload quota ({settings.youtube.daily_upload_quota}) already used today."
        )

    video_path = Path(media_file["path"])
    hashtags = json.loads(media_file["draft_hashtags"]) if media_file["draft_hashtags"] else []
    tags = json.loads(media_file["draft_tags"]) if media_file["draft_tags"] else []
    is_clip = media_file["kind"] == "clip"

    if is_clip:
        video_path = prepared_clip(video_path, hook_text_for(media_file))
        privacy = settings.youtube.default_clip_visibility
        if "#Shorts" not in hashtags:
            hashtags = [*hashtags, "#Shorts"]
    else:
        privacy = settings.youtube.default_full_game_visibility

    title = _sanitize(media_file["draft_title"])[:100]  # YouTube's title length limit
    description = _sanitize(media_file["draft_description"] or "")
    # The metadata builder already ends descriptions with the hashtag block; only append
    # one for hand-written descriptions that lack it.
    if hashtags and "#" not in description:
        description = f"{description}\n\n{' '.join(hashtags)}"
    if not tags:
        tags = [h.lstrip("#") for h in hashtags]

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "20",  # Gaming
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }

    service = _build_service()
    media = MediaFileUpload(str(video_path), chunksize=10 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    try:
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(
                    "YouTube upload progress for media_file %s: %d%%",
                    media_file["id"],
                    int(status.progress() * 100),
                )
    except HttpError as exc:
        raise _friendly_upload_error(exc) from exc

    video_id = response["id"]
    quota.record_success("youtube")

    # Custom thumbnails only apply to regular videos (Shorts ignore them) and require the
    # channel's one-time phone verification -- treat failure as cosmetic, never fatal.
    if not is_clip and media_file["thumbnail_path"] and Path(media_file["thumbnail_path"]).exists():
        try:
            service.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(media_file["thumbnail_path"])
            ).execute()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Custom thumbnail upload failed for %s (channel may need phone verification "
                "at youtube.com/verify) -- video itself published fine.",
                video_id,
                exc_info=True,
            )

    return video_id
