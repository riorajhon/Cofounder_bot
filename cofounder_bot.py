"""
Cofounder Bot: fetches founder profiles from CofoundersLab and sends connection requests.
Runs every 5 minutes until it reaches the last seen profile.
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Paths
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
STATE_PATH = BASE_DIR / "state.json"

# API
SEARCH_URL = "https://cofounderslab.com/api/backend/founder/search"
CONNECT_URL = "https://cofounderslab.com/api/backend/connection/connect"
SEARCH_LIMIT = 20
SEARCH_PAGE = 1
LOOP_INTERVAL_SEC = 10 * 60  # 5 minutes


def load_env():
    """Load .env and return bearer token, message, optional last_profile_id, optional discord webhook."""
    load_dotenv(ENV_PATH)
    token = os.getenv("BEARER_TOKEN")
    message = os.getenv("MESSAGE")
    if not token:
        raise SystemExit("Missing BEARER_TOKEN in .env")
    if not message:
        raise SystemExit("Missing MESSAGE in .env")
    last_id = os.getenv("LAST_PROFILE_ID", "").strip()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    return token, message, last_id, webhook_url


def load_state():
    """Load last_profile_id from state file (or from env as fallback)."""
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return data.get("last_profile_id", "")
        except (json.JSONDecodeError, IOError):
            pass
    return ""


def save_state(last_profile_id: str):
    """Persist last_profile_id to state file."""
    STATE_PATH.write_text(
        json.dumps({"last_profile_id": last_profile_id}, indent=2),
        encoding="utf-8",
    )


def fetch_profiles(bearer_token: str):
    """GET founder search (page 1, limit 20). Returns list of profiles and total info."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {"limit": SEARCH_LIMIT, "page": SEARCH_PAGE}
    resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    profiles = data.get("profiles") or []
    return profiles, data.get("total"), data.get("totalPages")


def send_connect(bearer_token: str, profile_id: str, message: str):
    """POST connection request for a profile. Raises RequestException on failure."""
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }
    payload = {"message": message, "profile": profile_id}
    resp = requests.post(CONNECT_URL, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        try:
            err_body = resp.json()
            err_msg = err_body.get("message") or err_body.get("error") or str(err_body)
        except Exception:
            err_msg = resp.text or resp.reason
        raise requests.RequestException(
            f"{resp.status_code} {resp.reason}: {err_msg}"
        )
    return resp.json() if resp.content else {}


def send_discord_webhook(webhook_url: str, content: str):
    """POST batch result to Discord webhook."""
    if not webhook_url:
        return
    try:
        resp = requests.post(
            webhook_url,
            json={"content": content},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Discord webhook failed: {e}")


def run_cycle(bearer_token: str, message: str, last_profile_id: str, webhook_url: str = ""):
    """
    One cycle: fetch page 1, send connect for each profile until we hit last_profile_id,
    then update last_profile_id to the first profile in this batch.
    """
    profiles, total, total_pages = fetch_profiles(bearer_token)
    if not profiles:
        print("No profiles returned.")
        return last_profile_id

    first_id = profiles[0]["_id"]
    sent = 0
    connected = []  # list of (id, firstName, lastName) for Discord
    for p in profiles:
        pid = p["_id"]
        if pid == last_profile_id:
            print(f"Reached last seen profile {pid}, stopping this batch.")
            break
        name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip() or pid
        try:
            send_connect(bearer_token, pid, message)
            sent += 1
            connected.append((pid, p.get("firstName", ""), p.get("lastName", "")))
            print(f"Connected: {name} ({pid})")
            time.sleep(1)
        except requests.RequestException as e:
            print(f"Failed to connect to {name} ({pid}): {e}")

    # Next time, stop when we see this batch's first profile
    new_last = first_id
    save_state(new_last)
    print(f"Sent {sent} connection(s). Updated last_profile_id to {new_last}.")

    # Notify Discord with batch result only when at least one connection was sent
    if sent > 0:
        names = [f"{first} {last}".strip() or pid for pid, first, last in connected]
        discord_msg = f"{sent} connected: {', '.join(names)}"
        send_discord_webhook(webhook_url, discord_msg)

    return new_last


def main():
    token, message, env_last_id, webhook_url = load_env()
    last_profile_id = load_state() or env_last_id

    print("Cofounder bot started. Running every 5 minutes. Ctrl+C to stop.")
    while True:
        try:
            last_profile_id = run_cycle(token, message, last_profile_id, webhook_url)
        except requests.RequestException as e:
            print(f"Request error: {e}")
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        time.sleep(LOOP_INTERVAL_SEC)


if __name__ == "__main__":
    main()
