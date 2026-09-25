"""Check Google Sheet and Telegram configuration before running Kpler worker.

Use --send-test to post one clearly labeled test message to the configured chat.
No Kpler requests, vessel updates, or sheet writes are performed.
"""
import argparse
import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from gsheet_handler import GSheet_Handler, get_all_ships

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "secrets.env")


def telegram_call(method, payload):
    token = os.environ.get("TELEGRAM_BOT_KEY", "")
    if not token or token == "YOUR_BOT_TOKEN":
        raise RuntimeError("Telegram bot token is missing")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload, timeout=15,
        )
        # Never include response URLs or bodies in errors: they may expose secrets.
        if response.status_code != 200:
            raise RuntimeError(f"Telegram {method} failed (HTTP {response.status_code})")
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} was rejected")
        return data["result"]
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"Telegram {method} request failed; check bot token and connection") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-test", action="store_true", help="Post one test message to configured Telegram group")
    args = parser.parse_args()

    worksheet = GSheet_Handler(use_streamlit=False).sheet
    ships, _ = get_all_ships(worksheet)
    print(f"Google Sheets: connected to tab '{worksheet.title}'; {len(ships)} vessel row(s)")
    problems = []
    for row_index, ship in ships:
        try:
            datetime.strptime(str(ship.get("Departure", "")), "%Y-%m-%d")
        except ValueError:
            problems.append(f"row {row_index}: Departure must be YYYY-MM-DD text")
        for key, lower, upper in (("Original_Dest_Lat", -90, 90), ("Original_Dest_Long", -180, 180)):
            try:
                if not lower <= float(ship.get(key, "")) <= upper:
                    raise ValueError
            except (ValueError, TypeError):
                problems.append(f"row {row_index}: {key} is missing or invalid")
        try:
            int(str(ship.get("KPLER_ID", "")))
        except ValueError:
            problems.append(f"row {row_index}: KPLER_ID must be numeric")
    if problems:
        for problem in problems:
            print("Fix:", problem)
        raise SystemExit(1)
    print("Watchlist fields: ready for dry run")

    bot = telegram_call("getMe", {})
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    if not chat_id:
        raise RuntimeError("Telegram target chat ID is missing")
    chat = telegram_call("getChat", {"chat_id": chat_id})
    print(f"Telegram: bot @{bot.get('username', '?')} can access '{chat.get('title', 'chat')}'")
    if args.send_test:
        telegram_call("sendMessage", {
            "chat_id": chat_id,
            "text": "TEST — Vessel diversion monitor setup. This is a connectivity check, not a real diversion alert.",
        })
        print("Telegram: test message sent")
    else:
        print("Telegram: no message sent; run with --send-test to test posting")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Setup check failed: {exc}")
        raise SystemExit(1)
