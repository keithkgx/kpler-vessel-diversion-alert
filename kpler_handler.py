"""Kpler position retrieval using an authorized, manually provisioned refresh token.

This worker does not perform an unattended password/MFA login. Confirm that the
endpoint and token grant are supported for your organization's Kpler access.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent
# A persistent volume path can be supplied by the host; local runs use the
# original project file. Never store a rotating token in a deployment image.
TOKEN_FILE = Path(os.getenv("KPLER_TOKEN_FILE", str(BASE_DIR / "kpler_tokens.json"))).expanduser()
TOKEN_URL = "https://kpler-prod.eu.auth0.com/oauth/token"
DEFAULT_CLIENT_ID = "RD0LrdwB4uu1NcQ8x6WgwTPlJYvaQXm7"  # inherited public client ID
MAX_STORED_POSITIONS = 180
RECENT_POSITIONS = 75


class KplerAuthenticationRequired(RuntimeError):
    """The operator must obtain an authorized replacement refresh token."""


def load_tokens():
    if not TOKEN_FILE.is_file():
        raise KplerAuthenticationRequired(
            "kpler_tokens.json is missing; run reauthorize_kpler.py interactively "
            "using an authorized Kpler account"
        )
    try:
        data = json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError) as exc:
        raise KplerAuthenticationRequired("Token file cannot be read") from exc
    if not isinstance(data, dict) or not data.get("refresh_token"):
        raise KplerAuthenticationRequired("Token file has no refresh_token")
    return data


def save_tokens(data):
    """Write rotated refresh tokens atomically and restrict file permissions."""
    fd, path = tempfile.mkstemp(prefix=".kpler_tokens_", dir=TOKEN_FILE.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, TOKEN_FILE)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def sample_positions(positions):
    """Retain the whole voyage shape plus uninterrupted recent pings for scoring.

    The Sheet has a per-cell size limit, so saving every AIS position from a
    long voyage would fail. Spread older samples across the entire route and
    retain the latest 75 consecutively for AIS gap and heading calculations.
    """
    if len(positions) <= MAX_STORED_POSITIONS:
        return positions
    older, recent = positions[:-RECENT_POSITIONS], positions[-RECENT_POSITIONS:]
    slots = MAX_STORED_POSITIONS - len(recent)
    indices = [round(i * (len(older) - 1) / (slots - 1)) for i in range(slots)]
    return [older[index] for index in indices] + recent


class KplerSession:
    def __init__(self):
        tokens = load_tokens()
        self.refresh_token = tokens["refresh_token"]
        # Refresh at startup; an inherited access token may already be expired.
        self.access_token = None
        self.token_expiry = None

    def ensure_access_token(self):
        now = datetime.now(timezone.utc)
        if self.access_token and self.token_expiry and now < self.token_expiry:
            return
        client_id = os.getenv("KPLER_CLIENT_ID", DEFAULT_CLIENT_ID).strip()
        if not client_id:
            raise KplerAuthenticationRequired("KPLER_CLIENT_ID is empty")
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(TOKEN_URL, json={
                    "grant_type": "refresh_token", "client_id": client_id,
                    "refresh_token": self.refresh_token,
                })
        except httpx.HTTPError as exc:
            raise RuntimeError("Kpler token request failed (network)") from exc
        if response.status_code in (400, 401, 403):
            # Only expose a known error code; never print the response body or tokens.
            try:
                error_code = response.json().get("error")
            except (ValueError, AttributeError):
                error_code = None
            safe_codes = {"invalid_grant", "invalid_client", "unauthorized_client", "access_denied"}
            detail = f", {error_code}" if error_code in safe_codes else ""
            raise KplerAuthenticationRequired(
                f"Kpler token refresh rejected (HTTP {response.status_code}{detail}); "
                "check for another session using the token, token expiry, "
                "and the approved client ID; a fresh authorization may be needed"
            )
        if response.status_code != 200:
            raise RuntimeError(f"Kpler token request failed (HTTP {response.status_code})")
        try:
            data = response.json()
            access = data["access_token"]
            refresh = data.get("refresh_token", self.refresh_token)
            expires_in = int(data.get("expires_in", 300))
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Kpler token response has an unexpected format") from exc
        # Do not log either token: a file timestamp does not prove rotation.
        logging.getLogger(__name__).info(
            "Kpler refresh token: returned=%s changed=%s",
            bool(data.get("refresh_token")), refresh != self.refresh_token,
        )
        # Persist first: refresh-token rotation may invalidate the previous token.
        save_tokens({"refresh_token": refresh})
        self.refresh_token = refresh
        self.access_token = access
        self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=max(1, expires_in - 30))

    def get_positions(self, vessel_id, departure_dt):
        self.ensure_access_token()
        departure = datetime.strptime(departure_dt, "%Y-%m-%d")
        params = {
            "after": departure.strftime("%Y-%m-%dT00:00:00.000Z"),
            "before": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            "limit": 5000,
        }
        headers = {
            "Referer": "https://terminal.kpler.com/", "Accept": "application/json",
            "x-access-token": self.access_token, "use-access-token": "true", "x-version": "1",
        }
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(
                    f"https://terminal.kpler.com/api/vessels/{int(vessel_id)}/positions",
                    params=params, headers=headers,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError("Kpler positions request failed (network)") from exc
        if response.status_code in (401, 403):
            raise KplerAuthenticationRequired(
                f"Kpler positions request rejected (HTTP {response.status_code})"
            )
        if response.status_code != 200:
            raise RuntimeError(f"Kpler positions request failed (HTTP {response.status_code})")
        positions = response.json()
        if not isinstance(positions, list):
            raise RuntimeError("Kpler positions response is not a list")
        fields = ("course", "heading", "geo", "receivedTime", "speed")
        valid = [p for p in positions if isinstance(p, dict)
                 and isinstance(p.get("geo"), dict)
                 and p["geo"].get("lat") is not None
                 and p["geo"].get("lon") is not None
                 and p.get("receivedTime")]
        valid.sort(key=lambda p: p["receivedTime"])
        if len(positions) >= params["limit"]:
            raise RuntimeError("Kpler returned the position limit; latest pings may be missing. Narrow voyage range")
        # Keep route-wide samples and the full recent window within one cell.
        window = sample_positions(valid)
        unique = {p["receivedTime"]: {key: p[key] for key in fields if key in p} for p in window}
        return [unique[timestamp] for timestamp in sorted(unique)]
