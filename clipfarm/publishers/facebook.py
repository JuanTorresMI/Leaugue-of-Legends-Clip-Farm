"""Facebook Page video + Reels publishing via raw Graph API calls (no maintained official SDK).

Full games -> regular Page video via the 3-phase resumable /{page-id}/videos flow
(start -> chunked transfer -> finish), all against graph.facebook.com.

Clips -> Reels via the dedicated 3-phase /{page-id}/video_reels flow (start -> upload bytes
to the returned rupload.facebook.com URL -> finish with video_state=PUBLISHED).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import requests

from clipfarm.publishers import quota

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
TRANSFER_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB
REELS_DAILY_LIMIT = 30  # Meta's documented cap on API-published Reels per page per 24h


class FacebookAuthError(RuntimeError):
    """The Page access token is missing, invalid, or expired."""


class FacebookQuotaExceededError(RuntimeError):
    """Local tracker says today's Reels publish cap (30/page/24h) is already used."""


def _credentials() -> tuple[str, str]:
    from clipfarm import fb_settings

    s = fb_settings.current()
    if not s.configured:
        raise FacebookAuthError(
            "Facebook Page ID / access token not set -- add them in the dashboard Facebook panel."
        )
    return s.page_id, s.access_token


def validate_credentials(page_id: str, token: str) -> str:
    """Confirm the token can read the Page; returns the Page name. Raises FacebookAuthError."""
    resp = requests.get(
        f"{GRAPH_API_BASE}/{page_id}", params={"fields": "name", "access_token": token}
    )
    if resp.status_code != 200:
        detail = resp.json().get("error", {}).get("message", resp.text[:200]) if resp.content else resp.text
        raise FacebookAuthError(f"Facebook rejected those credentials: {detail}")
    return resp.json().get("name", page_id)


def check_token() -> bool:
    """Health check for the CLI -- mirrors the Riot 'fail loudly' pattern."""
    try:
        page_id, token = _credentials()
        validate_credentials(page_id, token)
        return True
    except (FacebookAuthError, requests.RequestException):
        return False


def _raise_for_status(resp: requests.Response) -> dict:
    if resp.status_code != 200:
        raise RuntimeError(f"Facebook Graph API error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def publish_page_video(video_path: Path, title: str, description: str) -> str:
    """Uploads a regular Page video (used for full games) via the resumable start/transfer/finish flow."""
    page_id, token = _credentials()
    file_size = video_path.stat().st_size

    start_resp = _raise_for_status(
        requests.post(
            f"{GRAPH_API_BASE}/{page_id}/videos",
            data={"upload_phase": "start", "access_token": token, "file_size": file_size},
        )
    )
    video_id = start_resp["video_id"]
    upload_session_id = start_resp["upload_session_id"]
    start_offset = int(start_resp["start_offset"])
    end_offset = int(start_resp["end_offset"])

    with video_path.open("rb") as f:
        while start_offset != end_offset:
            f.seek(start_offset)
            chunk = f.read(min(TRANSFER_CHUNK_SIZE, end_offset - start_offset))
            # All three phases (start/transfer/finish) POST to the SAME /{page-id}/videos edge,
            # with upload_session_id in the body. POSTing to /{upload_session_id} instead hits a
            # generic object node and fails with "(#100) The parameter target is required".
            transfer_resp = _raise_for_status(
                requests.post(
                    f"{GRAPH_API_BASE}/{page_id}/videos",
                    data={
                        "upload_phase": "transfer",
                        "upload_session_id": upload_session_id,
                        "start_offset": start_offset,
                        "access_token": token,
                    },
                    files={"video_file_chunk": chunk},
                )
            )
            start_offset = int(transfer_resp["start_offset"])
            end_offset = int(transfer_resp["end_offset"])
            logger.info("Facebook video upload progress: %d/%d bytes", start_offset, file_size)

    _raise_for_status(
        requests.post(
            f"{GRAPH_API_BASE}/{page_id}/videos",
            data={
                "upload_phase": "finish",
                "upload_session_id": upload_session_id,
                "access_token": token,
                "title": title,
                "description": description,
            },
        )
    )
    return video_id


def publish_reel(video_path: Path, description: str) -> str:
    """Uploads a Reel (used for clips) via the dedicated video_reels start/upload/finish flow."""
    if not quota.check("facebook_reels", REELS_DAILY_LIMIT):
        raise FacebookQuotaExceededError(f"Facebook Reels daily limit ({REELS_DAILY_LIMIT}) already used today.")

    page_id, token = _credentials()

    start_resp = _raise_for_status(
        requests.post(
            f"{GRAPH_API_BASE}/{page_id}/video_reels",
            data={"upload_phase": "start", "access_token": token},
        )
    )
    video_id = start_resp["video_id"]
    upload_url = start_resp["upload_url"]

    file_size = video_path.stat().st_size
    with video_path.open("rb") as f:
        upload_resp = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(file_size),
            },
            data=f,
        )
    if upload_resp.status_code != 200:
        raise RuntimeError(f"Facebook Reels upload error {upload_resp.status_code}: {upload_resp.text[:500]}")

    _raise_for_status(
        requests.post(
            f"{GRAPH_API_BASE}/{page_id}/video_reels",
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "access_token": token,
                "video_state": "PUBLISHED",
                "description": description,
            },
        )
    )
    quota.record_success("facebook_reels")
    return video_id


def publish(media_file: sqlite3.Row) -> str:
    """Dispatches to the regular video flow for full games, Reels for clips."""
    from clipfarm.media.edit import hook_text_for, prepared_clip

    video_path = Path(media_file["path"])
    hashtags = json.loads(media_file["draft_hashtags"]) if media_file["draft_hashtags"] else []
    description = media_file["draft_description"] or ""
    # Drafts are written YouTube-first; on a Facebook Page the ask is Follow, not Subscribe.
    description = description.replace("SUBSCRIBE", "FOLLOW")
    if hashtags:
        description = f"{description}\n\n{' '.join(hashtags)}"

    if media_file["kind"] == "clip":
        # Reels need the same vertical/edited render as YouTube Shorts.
        return publish_reel(prepared_clip(video_path, hook_text_for(media_file)), description)
    return publish_page_video(video_path, media_file["draft_title"], description)
