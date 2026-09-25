"""Google Sheet access shared by the scheduled worker and optional Streamlit app."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

COLUMNS = [
    "Last_Updated", "IMO", "Name", "KPLER_ID", "Departure", "Coord_Trace",
    "Diversion_Flag", "Original_Dest", "Original_Dest_Lat",
    "Original_Dest_Long", "Cargo",
]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GSheet_Handler:
    def __init__(self, use_streamlit=False):
        if use_streamlit:
            import streamlit as st
            config = st.secrets
        else:
            load_dotenv(os.path.join(os.path.dirname(__file__), "secrets.env"))
            config = os.environ
        raw = config["GSHEET_CREDENTIALS"]
        credentials = json.loads(raw) if isinstance(raw, str) else dict(raw)
        auth = Credentials.from_service_account_info(credentials, scopes=SCOPES)
        self.client = gspread.authorize(auth)
        self.sheet = self.client.open_by_key(config["SPREADSHEET_ID"]).worksheet(config["SHEET_NAME"])


def parse_row(row, header):
    record = dict(zip(header, row + [""] * max(0, len(header) - len(row))))
    try:
        record["Coord_Trace"] = json.loads(record.get("Coord_Trace") or "[]")
    except (ValueError, TypeError):
        record["Coord_Trace"] = []
    return record


def get_all_ships(sheet):
    """Return (physical row number, record) pairs and the actual column map."""
    rows = sheet.get_all_values()
    if not rows:
        raise ValueError("Sheet is empty: add the required header row first")
    header = rows[0]
    missing = set(COLUMNS) - set(header)
    if missing or len(set(header)) != len(header):
        raise ValueError(f"Invalid sheet headers. Missing: {sorted(missing)}; duplicates are not allowed")
    header_map = {name: gspread.utils.rowcol_to_a1(1, i + 1).rstrip("1")
                  for i, name in enumerate(header)}
    ships = [(row_idx, parse_row(row, header))
             for row_idx, row in enumerate(rows[1:], start=2) if any(row)]
    return ships, header_map


def upsert_ship(sheet, row_index, updated_record, header_map):
    """Only write worker-owned cells; never overwrite the operator's watchlist."""
    values = {
        "Last_Updated": datetime.now(ZoneInfo("Asia/Singapore")).isoformat(timespec="seconds"),
        "Coord_Trace": json.dumps(updated_record["Coord_Trace"], separators=(",", ":")),
        "Diversion_Flag": bool(updated_record["Diversion_Flag"]),
    }
    changes = [{"range": f"{header_map[key]}{row_index}", "values": [[value]]}
               for key, value in values.items()]
    sheet.batch_update(changes, value_input_option="RAW")
