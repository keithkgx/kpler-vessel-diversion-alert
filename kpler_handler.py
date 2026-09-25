"""Kpler positions with rotating refresh tokens and optional login recovery."""
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
DEFAULT_CLIENT_ID = "0LglhXfJvfepANl3HqVT9i1U0OwV0gSP"  # current repository default
MAX_STORED_POSITIONS = 180
RECENT_POSITIONS = 75
SAFE_ERROR_CODES = {"invalid_grant", "invalid_client", "unauthorized_client", "access_denied"}


def _error_code(response):
    """Extract a known Auth0 code without exposing the response body."""
    try:
        body = response.json()
        return body.get("error") if isinstance(body, dict) else None
    except (ValueError, TypeError, AttributeError):
        return None


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
                if response.status_code in (400, 401, 403) and _error_code(response) == "invalid_grant":
                    # The inherited worker obtained a new session with a password
                    # grant when its refresh-token chain became invalid. Do this
                    # only once, and only when unattended credentials are configured.
                    email = (os.getenv("KPLER_EMAIL") or os.getenv("EMAIL") or "").strip()
                    password = os.getenv("KPLER_PASSWORD") or os.getenv("PASSWORD") or ""
                    if not email or not password:
                        raise KplerAuthenticationRequired(
                            "Kpler refresh token was rejected (invalid_grant); set "
                            "KPLER_EMAIL and KPLER_PASSWORD on the worker to enable "
                            "automatic login recovery, or reauthorize interactively"
                        )
                    logging.getLogger(__name__).warning(
                        "Kpler refresh token rejected; attempting one password login"
                    )
                    response = client.post(TOKEN_URL, json={
                        "grant_type": "password", "client_id": client_id,
                        "username": email, "password": password,
                        "audience": "https://terminal.kpler.com",
                        "scope": "openid profile email offline_access",
                    })
                    if response.status_code != 200:
                        code = _error_code(response)
                        if code == "mfa_required":
                            raise KplerAuthenticationRequired(
                                "Kpler requires MFA for a new login; run "
                                "reauthorize_kpler.py in an interactive terminal"
                            )
                        detail = f", {code}" if code in SAFE_ERROR_CODES else ""
                        raise KplerAuthenticationRequired(
                            f"Kpler password login rejected (HTTP {response.status_code}{detail}); "
                            "check that this client permits password login and the "
                            "account is authorized"
                        )
                    # A successful login returns an access token and a new refresh
                    # token. Persist the refresh token before making any API request.
                    login_recovery = True
                else:
                    login_recovery = False
        except httpx.HTTPError as exc:
            raise RuntimeError("Kpler token request failed (network)") from exc
        if response.status_code in (400, 401, 403):
            # Only expose a known error code; never print the response body or tokens.
            error_code = _error_code(response)
            detail = f", {error_code}" if error_code in SAFE_ERROR_CODES else ""
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
            if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
                raise ValueError("Missing tokens")
            if login_recovery and not data.get("refresh_token"):
                raise ValueError("Login returned no refresh token")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeError("Kpler token response has an unexpected format") from exc
        # Do not log either token: a file timestamp does not prove rotation.
        logging.getLogger(__name__).info(
            "Kpler %s token: returned=%s changed=%s",
            "login recovery" if login_recovery else "refresh",
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
