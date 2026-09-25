"""Local one-time setup: never commit the output files or print their contents."""
import getpass
import json
import os
import shlex
from pathlib import Path

base = Path(__file__).resolve().parent
raw_path = input("Path to fresh Google service-account JSON: ").strip()
# Windows Explorer's "Copy as path" surrounds paths with double quotes.
path = Path(raw_path.strip('"\'')).expanduser()
if not raw_path:
    raise ValueError("Enter the path to the Google service-account JSON")
credentials = json.loads(path.read_text())
for field in ("client_email", "private_key", "token_uri"):
    if not credentials.get(field):
        raise ValueError(f"Google JSON is missing {field}")
spreadsheet_id = input("Google spreadsheet ID: ").strip()
sheet_name = input("Worksheet/tab name [Watchlist]: ").strip() or "Watchlist"
bot_token = getpass.getpass("Telegram bot token: ").strip()
chat_id = input("Telegram target chat ID: ").strip()
dashboard_url = input("Dashboard URL [leave blank until deployed]: ").strip()
dashboard_password = getpass.getpass("Optional dashboard password [blank for private-host-only]: ")
if not all((spreadsheet_id, bot_token, chat_id)):
    raise ValueError("Spreadsheet ID, bot token and chat ID are required")

pairs = {
    "GSHEET_CREDENTIALS": json.dumps(credentials, separators=(",", ":")),
    "SPREADSHEET_ID": spreadsheet_id,
    "SHEET_NAME": sheet_name,
    "TELEGRAM_BOT_KEY": bot_token,
    "TELEGRAM_CHANNEL_ID": chat_id,
    "DASHBOARD_URL": dashboard_url,
}

def private_write(destination, content):
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)

private_write(base / "secrets.env", "".join(f"{k}={shlex.quote(v)}\n" for k, v in pairs.items()))
streamlit_dir = base / ".streamlit"
streamlit_dir.mkdir(exist_ok=True)
app_secrets = {key: pairs[key] for key in ("GSHEET_CREDENTIALS", "SPREADSHEET_ID", "SHEET_NAME")}
if dashboard_password:
    app_secrets["DASHBOARD_PASSWORD"] = dashboard_password
private_write(streamlit_dir / "secrets.toml", "".join(f"{k} = {json.dumps(v)}\n" for k, v in app_secrets.items()))
print("Created secrets.env and .streamlit/secrets.toml with restricted permissions.")
