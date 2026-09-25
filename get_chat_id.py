"""Find a Telegram group chat ID without printing or storing the bot token."""
import getpass
import requests

bot_token = getpass.getpass("Telegram bot token (hidden): ").strip()
if not bot_token:
    raise SystemExit("No bot token entered")
try:
    response = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=15,
    )
    # Do not print the request URL: it contains the bot token.
    response.raise_for_status()
    updates = response.json().get("result", [])
except (requests.RequestException, ValueError) as exc:
    raise SystemExit("Could not retrieve Telegram updates. Check the bot token and connection.") from None

found = set()
for update in updates:
    for event_type in ("message", "my_chat_member", "edited_message"):
        chat = (update.get(event_type) or {}).get("chat") or {}
        if chat.get("type") in ("group", "supergroup") and chat.get("id"):
            found.add((chat.get("title", "Unnamed group"), chat["id"]))
if found:
    for title, chat_id in sorted(found):
        print(f"Group: {title} | Chat ID: {chat_id}")
else:
    print("No group updates yet. Send /start@YourBotUsername in the group, then rerun this script.")
