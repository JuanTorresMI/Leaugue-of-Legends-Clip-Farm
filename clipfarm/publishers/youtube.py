"""YouTube uploads via OAuth installed-app flow + resumable videos.insert."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
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
# dashboard; youtube.force-ssl = the comments API (the auto-posted engagement comment -- Google
# gates all comment endpoints behind this one scope). SCOPES is what `reauth-youtube` requests;
# an existing token keeps working for everything it was granted until the next reauth.
_COMMENTS_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    _COMMENTS_SCOPE,
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


# A freshly inserted video can briefly 404 on thumbnails.set until YouTube registers it.
_THUMBNAIL_RETRY_DELAYS = (5, 15, 30)


def _error_reason(exc: HttpError) -> str | None:
    try:
        return json.loads(exc.content.decode())["error"]["errors"][0].get("reason")
    except Exception:  # noqa: BLE001
        return None


def _set_thumbnail(service, video_id: str, thumbnail_path: str, sleep=time.sleep) -> bool:
    """Upload a custom thumbnail, retrying while YouTube hasn't registered the new video yet.
    Never raises: returns True on success, logs the real reason and returns False otherwise."""
    for attempt in range(len(_THUMBNAIL_RETRY_DELAYS) + 1):
        try:
            service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumbnail_path)).execute()
            return True
        except HttpError as exc:
            reason = _error_reason(exc)
            if reason == "videoNotFound" and attempt < len(_THUMBNAIL_RETRY_DELAYS):
                sleep(_THUMBNAIL_RETRY_DELAYS[attempt])
                continue
            hint = {
                "videoNotFound": "YouTube still hadn't registered the video after retrying",
                "forbidden": "the channel may need phone verification at youtube.com/verify",
            }.get(reason, f"YouTube said: {reason or exc}")
            logger.warning("Custom thumbnail not set for %s (%s) -- video itself published fine.", video_id, hint)
            return False
        except Exception:  # noqa: BLE001
            logger.warning("Custom thumbnail not set for %s -- video itself published fine.", video_id, exc_info=True)
            return False
    return False


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
    # Load with the token file's own scopes (not SCOPES): refreshing a token while requesting
    # scopes it was never granted makes Google reject the refresh outright, which would break
    # uploads the moment a new scope lands in SCOPES. New scopes apply on the next reauth.
    creds = Credentials.from_authorized_user_file(str(token_path))
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


def _find_or_create_playlist(service, title: str, description: str) -> str:
    """Id of the channel's playlist with this exact title, creating it (public) if missing."""
    page_token = None
    while True:
        resp = service.playlists().list(
            part="snippet", mine=True, maxResults=50, pageToken=page_token
        ).execute()
        for item in resp.get("items", []):
            if item["snippet"]["title"] == title:
                return item["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    created = service.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": description, "defaultLanguage": "en"},
            "status": {"privacyStatus": "public"},
        },
    ).execute()
    return created["id"]


def _add_to_champion_playlist(service, media_file: sqlite3.Row, video_id: str) -> None:
    """File a full game into its champion's playlist (created on first use). Playlists turn a
    one-off viewer into a session: YouTube chains playlist videos in Up Next, and a "<Champ>
    Full Games" playlist is exactly what a champion-curious searcher binges."""
    from clipfarm.riot.champion_names import display_name

    champion = media_file["champion"]
    if not champion:
        return
    champ = display_name(champion)
    playlist_id = _find_or_create_playlist(
        service,
        f"{champ} Full Games",
        f"Full {champ} ranked games, unedited. New games added automatically.",
    )
    service.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def _comment_text_for(settings, video_id: str) -> str:
    """The engagement comment for this upload. Rotates deterministically (by video id) through
    the configured pool so the channel isn't posting the exact same sentence several times a
    day -- identical repeated comments read as spam to viewers and to YouTube's filter."""
    import zlib

    pool = [t.strip() for t in settings.youtube.auto_comment_texts if t.strip()]
    if not pool and settings.youtube.auto_comment_text.strip():
        pool = [settings.youtube.auto_comment_text.strip()]
    if not pool:
        return ""
    return pool[zlib.crc32(video_id.encode()) % len(pool)]


def _post_engagement_comment(service, video_id: str, text: str) -> None:
    """Drop the channel's own first comment under a fresh upload. Comments are one of the few
    engagement signals we can seed ourselves, and a question invites replies -- every reply is
    a ranking signal the video wouldn't otherwise get. Requires the youtube.force-ssl scope."""
    service.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        },
    ).execute()


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

    creds = _load_credentials()
    service = build("youtube", "v3", credentials=creds)
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

    # Cosmetic, never fatal. For Shorts, YouTube only *displays* a custom thumbnail on Partner
    # Program channels (rolled out July 2026), and even then only on search / channel grid /
    # subscriptions -- never the swipe feed. Other channels get the upload accepted but a video
    # frame shown. Still worth sending: it starts working the day the channel joins YPP.
    if media_file["thumbnail_path"] and Path(media_file["thumbnail_path"]).exists():
        _set_thumbnail(service, video_id, media_file["thumbnail_path"])

    # File full games into their champion's playlist (session-building; see the helper).
    # Cosmetic like the thumbnail: a playlist hiccup must never fail a finished upload.
    if not is_clip:
        try:
            _add_to_champion_playlist(service, media_file, video_id)
        except Exception:  # noqa: BLE001
            logger.warning("Playlist filing failed for %s -- video itself published fine.",
                           video_id, exc_info=True)

    # Seed the channel's own first comment (a question CTA). Cosmetic like the thumbnail and
    # playlist: a comment hiccup must never fail a finished upload. Tokens minted before the
    # comments scope was added skip quietly until the next `reauth-youtube`.
    comment_text = _comment_text_for(settings, video_id)
    if comment_text:
        if _COMMENTS_SCOPE in (creds.scopes or []):
            try:
                _post_engagement_comment(service, video_id, comment_text)
            except Exception:  # noqa: BLE001
                logger.warning("Auto-comment failed for %s -- video itself published fine.",
                               video_id, exc_info=True)
        else:
            logger.info(
                "Skipping auto-comment for %s: the cached token predates the comments scope -- "
                "run `python -m clipfarm.cli reauth-youtube` once to enable it.", video_id,
            )

    return video_id
