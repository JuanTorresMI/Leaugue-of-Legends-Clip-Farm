"""One-time interactive OAuth consent for YouTube uploads.

Run via: python -m clipfarm.cli reauth-youtube
Needs GOOGLE_CLIENT_SECRETS_PATH in .env pointing at a Desktop-app OAuth Client ID JSON
downloaded from https://console.cloud.google.com (see README.md for the full setup steps).
"""
from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from clipfarm.config import get_settings
from clipfarm.publishers.youtube import SCOPES


def run_reauth() -> None:
    settings = get_settings()
    secrets_path = settings.secrets.google_client_secrets_path
    if not secrets_path or not Path(secrets_path).exists():
        print(
            f"Google client secrets not found at {secrets_path!r}. Set GOOGLE_CLIENT_SECRETS_PATH "
            "in .env to a downloaded OAuth Client ID JSON (Desktop app type) from "
            "https://console.cloud.google.com."
        )
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = settings.project_root / "data" / "youtube_token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"Saved YouTube credentials to {token_path}")


if __name__ == "__main__":
    run_reauth()
