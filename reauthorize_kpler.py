"""Interactively obtain a fresh Kpler session for an authorized account.

The login uses the same Auth0 password and MFA grants as the inherited worker.
It runs only when an operator starts this script in a terminal. Passwords and
one-time codes are never saved; both returned tokens and the access expiry are
stored by the existing Kpler handler for later worker runs.
"""

import getpass
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from kpler_handler import BASE_DIR, DEFAULT_CLIENT_ID, TOKEN_URL, save_tokens


AUTH_BASE = TOKEN_URL.rsplit("/oauth/token", 1)[0]
AUDIENCE = "https://terminal.kpler.com"
SCOPE = "openid profile email offline_access"
SAFE_ERRORS = {"invalid_grant", "invalid_client", "unauthorized_client", "access_denied", "mfa_required"}


class KplerLoginError(RuntimeError):
    """A login failed without exposing server responses or submitted secrets."""


def _error(response, context):
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError, TypeError):
        error = None
    suffix = f", {error}" if error in SAFE_ERRORS else ""
    return KplerLoginError(f"{context} (HTTP {response.status_code}{suffix})")


def _tokens(response):
    if response.status_code != 200:
        raise _error(response, "Kpler login rejected")
    try:
        data = response.json()
        if not isinstance(data["access_token"], str) or not data["access_token"]:
            raise ValueError("Missing access token")
        if not isinstance(data["refresh_token"], str) or not data["refresh_token"]:
            raise ValueError("Missing refresh token")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise KplerLoginError("Kpler login did not return the required tokens") from exc
    return data


def login_and_save(email, password, client_id, *, prompt=input, secret_prompt=getpass.getpass):
    """Run an interactive password/MFA login, saving the resulting session."""
    if not email or not password or not client_id:
        raise KplerLoginError("Email, password and Kpler client ID are required")

    try:
        with httpx.Client(timeout=25) as client:
            response = client.post(TOKEN_URL, json={
                "grant_type": "password", "client_id": client_id,
                "username": email, "password": password,
                "audience": AUDIENCE, "scope": SCOPE,
            })
            if response.status_code == 200:
                tokens = _tokens(response)
            else:
                try:
                    body = response.json()
                except (ValueError, AttributeError):
                    body = {}
                if not isinstance(body, dict) or body.get("error") != "mfa_required":
                    raise _error(response, "Kpler password login rejected")
                mfa_token = body.get("mfa_token")
                if not isinstance(mfa_token, str) or not mfa_token:
                    raise KplerLoginError("Kpler requested MFA without an MFA token")

                method = prompt("MFA method: 1 = authenticator app, 2 = email code: ").strip()
                if method == "1":
                    code = secret_prompt("Authenticator code (hidden): ").strip()
                    if not code:
                        raise KplerLoginError("An authenticator code is required")
                    result = client.post(TOKEN_URL, json={
                        "grant_type": "http://auth0.com/oauth/grant-type/mfa-otp",
                        "client_id": client_id, "mfa_token": mfa_token, "otp": code,
                    })
                elif method == "2":
                    enrolled = client.get(
                        f"{AUTH_BASE}/mfa/authenticators",
                        headers={"Authorization": f"Bearer {mfa_token}"},
                    )
                    if enrolled.status_code != 200:
                        raise _error(enrolled, "Could not list MFA authenticators")
                    try:
                        authenticators = enrolled.json()
                        email_factor = next(a for a in authenticators
                                            if isinstance(a, dict) and a.get("active")
                                            and a.get("oob_channel") == "email" and a.get("id"))
                    except (ValueError, StopIteration, TypeError):
                        raise KplerLoginError("No active email MFA authenticator was found") from None
                    challenge = client.post(f"{AUTH_BASE}/mfa/challenge", json={
                        "client_id": client_id, "mfa_token": mfa_token,
                        "challenge_type": "oob", "authenticator_id": email_factor["id"],
                    })
                    if challenge.status_code != 200:
                        raise _error(challenge, "Kpler email MFA challenge failed")
                    try:
                        oob_code = challenge.json()["oob_code"]
                    except (ValueError, KeyError, TypeError) as exc:
                        raise KplerLoginError("Email MFA challenge did not return a code reference") from exc
                    code = secret_prompt("Code received by email (hidden): ").strip()
                    if not code:
                        raise KplerLoginError("An email MFA code is required")
                    result = client.post(TOKEN_URL, json={
                        "grant_type": "http://auth0.com/oauth/grant-type/mfa-oob",
                        "client_id": client_id, "mfa_token": mfa_token,
                        "oob_code": oob_code, "binding_code": code,
                    })
                else:
                    raise KplerLoginError("Choose MFA method 1 or 2")
                tokens = _tokens(result)
    except httpx.HTTPError as exc:
        raise KplerLoginError("Kpler login failed due to a network error") from exc

    # Never overwrite the working token file if login, MFA, or parsing fails.
    try:
        expires_in = int(tokens.get("expires_in", 300))
    except (ValueError, TypeError) as exc:
        raise KplerLoginError("Kpler login returned an invalid access expiry") from exc
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, expires_in))
    save_tokens({
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens["access_token"],
        "expires_at": expires_at.isoformat(),
        "client_id": client_id,
    })


def main():
    load_dotenv(BASE_DIR / "secrets.env")
    if not sys.stdin.isatty():
        raise KplerLoginError("Run this script in an interactive terminal to enter credentials and MFA")
    client_id = os.getenv("KPLER_CLIENT_ID", DEFAULT_CLIENT_ID).strip()
    email = input("Authorized Kpler account email: ").strip()
    password = getpass.getpass("Kpler password (hidden): ")
    try:
        login_and_save(email, password, client_id)
    finally:
        del password
    print("Kpler login succeeded. Session tokens saved; password and MFA codes were not stored.")


if __name__ == "__main__":
    try:
        main()
    except (KplerLoginError, EOFError, KeyboardInterrupt) as exc:
        message = str(exc) if isinstance(exc, KplerLoginError) else "Login cancelled"
        print(message, file=sys.stderr)
        raise SystemExit(1) from None
