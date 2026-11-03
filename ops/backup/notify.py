"""Send fixed backup alerts without logging hook URLs, tokens, or request data."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MESSAGES = {
    "failure": "Ajo backup operation failed.",
    "freshness": "Ajo backup freshness check is overdue.",
    "size-warning": "Ajo backup storage is approaching its configured size limit.",
    "heartbeat": "Ajo encrypted backup completed successfully.",
}


def _post(url: str, payload: dict[str, str]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except (OSError, urllib.error.URLError, ValueError):
        # Hook URLs and Telegram bot tokens are credentials; never print exceptions.
        pass


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in MESSAGES:
        return 2
    event = sys.argv[1]
    message = MESSAGES[event]
    slack_url = os.environ.get("BACKUP_SLACK_WEBHOOK_URL", "")
    bot_token = os.environ.get("BACKUP_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("BACKUP_TELEGRAM_CHAT_ID", "")

    if event != "heartbeat" and slack_url:
        _post(slack_url, {"text": message})
    if bot_token and chat_id:
        telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        _post(telegram_url, {"chat_id": chat_id, "text": message})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
