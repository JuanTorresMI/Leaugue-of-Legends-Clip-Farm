"""One-time interactive setup for a durable Facebook Page token that can read insights.

Run via: python -m clipfarm.cli reauth-facebook

The Facebook parallel to `reauth-youtube`. YouTube uses a browser OAuth flow; Facebook has no
installed-app flow, so this walks you through the manual-but-durable path:

  1. You grant your app the needed permissions in Graph API Explorer -- crucially INCLUDING
     `read_insights`, the permission that unlocks video analytics (the FB equivalent of the
     YouTube analytics scope). Copy the short-lived **User** token it gives you.
  2. This script exchanges that short-lived user token for a long-lived one (needs App ID +
     App Secret), then reads your Page's access token from /me/accounts. A Page token derived
     from a long-lived user token does not expire.
  3. It verifies the token actually carries read_insights, then saves page_id + access_token to
     data/facebook.json (the dashboard's live source of truth).

App ID / App Secret are read from .env (FB_APP_ID / FB_APP_SECRET) if present, else prompted.
Get them at https://developers.facebook.com/apps -> your app -> Settings -> Basic.
"""
from __future__ import annotations

import sys

import requests

from clipfarm import fb_settings
from clipfarm.config import get_settings

GRAPH = "https://graph.facebook.com/v21.0"
REQUIRED_PERMS = ("pages_show_list", "pages_read_engagement", "pages_manage_posts", "read_insights")


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default[:6]}...]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or (default or "")


def _get(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=30)
    body = resp.json() if resp.content else {}
    if resp.status_code != 200 or "error" in body:
        msg = body.get("error", {}).get("message", resp.text[:300])
        raise SystemExit(f"Facebook API error ({resp.status_code}): {msg}")
    return body


def run_reauth() -> None:
    settings = get_settings()
    secrets = settings.secrets

    print(__doc__)
    print("-" * 78)
    print(
        "Step 1: open https://developers.facebook.com/tools/explorer, pick your app, and add\n"
        "these permissions, then click 'Generate Access Token' and sign in:\n"
        "    " + ", ".join(REQUIRED_PERMS) + "\n"
        "Copy the User token it produces (starts with 'EAA...').\n"
    )

    app_id = secrets.fb_app_id or _prompt("FB App ID")
    app_secret = secrets.fb_app_secret or _prompt("FB App Secret")
    short_user_token = _prompt("Short-lived USER token from Graph API Explorer")
    if not (app_id and app_secret and short_user_token):
        raise SystemExit("App ID, App Secret, and a user token are all required. Aborting.")

    print("\nExchanging for a long-lived user token...")
    long_lived = _get(
        f"{GRAPH}/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_user_token,
        },
    )
    user_token = long_lived["access_token"]

    print("Fetching your Pages and their (non-expiring) Page tokens...")
    accounts = _get(f"{GRAPH}/me/accounts", {"access_token": user_token, "fields": "id,name,access_token"})
    pages = accounts.get("data", [])
    if not pages:
        raise SystemExit(
            "That user has no Pages (or the token lacks pages_show_list). Grant the permissions "
            "in Step 1 and try again."
        )

    want_id = fb_settings.current().page_id or secrets.fb_page_id
    page = next((p for p in pages if p.get("id") == want_id), None)
    if page is None:
        if len(pages) == 1:
            page = pages[0]
        else:
            print("\nMultiple Pages found:")
            for i, p in enumerate(pages):
                print(f"  [{i}] {p.get('name')} ({p.get('id')})")
            page = pages[int(_prompt("Pick a Page number", "0") or "0")]

    page_id = page["id"]
    page_token = page["access_token"]

    # Confirm the durable Page token actually carries read_insights before we save it.
    dbg = _get(f"{GRAPH}/debug_token", {"input_token": page_token, "access_token": user_token})
    scopes = dbg.get("data", {}).get("scopes", [])
    expires = dbg.get("data", {}).get("expires_at", 0)
    has_insights = "read_insights" in scopes

    fb_settings.save(
        fb_settings.FacebookSettings(page_id=page_id, access_token=page_token, enabled=fb_settings.current().enabled)
    )

    print("\n" + "=" * 78)
    print(f"Saved durable Page token for '{page.get('name')}' ({page_id}) to data/facebook.json")
    print(f"  expires        : {'never' if not expires else expires}")
    print(f"  scopes         : {', '.join(scopes) or '(none reported)'}")
    print(f"  read_insights  : {'YES -- analytics enabled' if has_insights else 'MISSING'}")
    if not has_insights:
        print(
            "\n  WARNING: read_insights is not on this token, so video analytics will still 403.\n"
            "  Redo Step 1 and make sure read_insights is checked before generating the token."
        )
    print("=" * 78)
    sys.exit(0 if has_insights else 2)


if __name__ == "__main__":
    run_reauth()
