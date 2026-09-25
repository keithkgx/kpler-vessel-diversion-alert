import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import kpler_handler


class KplerAuthTests(unittest.TestCase):
    def test_invalid_grant_recovers_once_and_persists_new_refresh(self):
        calls = []

        class Response:
            def __init__(self, status, body):
                self.status_code, self.body = status, body

            def json(self):
                return self.body

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, json):
                calls.append(json)
                if json["grant_type"] == "refresh_token":
                    return Response(403, {"error": "invalid_grant"})
                return Response(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 600})

        with patch.dict("os.environ", {"KPLER_CLIENT_ID": "authorized-client", "KPLER_EMAIL": "test@example.com", "KPLER_PASSWORD": "private-password"}), \
             patch.object(kpler_handler, "load_tokens", return_value={"refresh_token": "old-refresh"}), \
             patch.object(kpler_handler, "save_tokens") as save, \
             patch.object(kpler_handler.httpx, "Client", Client):
            session = kpler_handler.KplerSession()
            session.ensure_access_token()
            session.ensure_access_token()

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["grant_type"], "password")
        self.assertEqual(calls[1]["client_id"], "authorized-client")
        self.assertEqual(calls[1]["username"], "test@example.com")
        self.assertEqual(session.access_token, "new-access")
        save.assert_called_once_with({"refresh_token": "new-refresh"})

    def test_login_recovery_rejection_keeps_token_and_hides_credentials(self):
        calls = []

        class Response:
            def __init__(self, status, error):
                self.status_code, self.error = status, error

            def json(self):
                return {"error": self.error, "error_description": "private-description"}

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, json):
                calls.append(json)
                return Response(403, "invalid_grant") if len(calls) == 1 else Response(401, "access_denied")

        with patch.dict("os.environ", {"KPLER_EMAIL": "test@example.com", "KPLER_PASSWORD": "private-password"}), \
             patch.object(kpler_handler, "load_tokens", return_value={"refresh_token": "old-refresh"}), \
             patch.object(kpler_handler, "save_tokens") as save, \
             patch.object(kpler_handler.httpx, "Client", Client):
            with self.assertRaises(kpler_handler.KplerAuthenticationRequired) as failure:
                kpler_handler.KplerSession().ensure_access_token()

        self.assertEqual(len(calls), 2)
        save.assert_not_called()
        self.assertIn("password login rejected", str(failure.exception))
        self.assertNotIn("private-password", str(failure.exception))
        self.assertNotIn("private-description", str(failure.exception))

    def test_mfa_requirement_stops_unattended_recovery(self):
        class Response:
            def __init__(self, status, error):
                self.status_code, self.error = status, error

            def json(self):
                return {"error": self.error, "mfa_token": "private-mfa"}

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, json):
                return Response(403, "invalid_grant") if json["grant_type"] == "refresh_token" else Response(403, "mfa_required")

        with patch.dict("os.environ", {"KPLER_EMAIL": "test@example.com", "KPLER_PASSWORD": "private-password"}), \
             patch.object(kpler_handler, "load_tokens", return_value={"refresh_token": "old-refresh"}), \
             patch.object(kpler_handler, "save_tokens") as save, \
             patch.object(kpler_handler.httpx, "Client", Client):
            with self.assertRaises(kpler_handler.KplerAuthenticationRequired) as failure:
                kpler_handler.KplerSession().ensure_access_token()

        self.assertIn("MFA", str(failure.exception))
        self.assertNotIn("private-mfa", str(failure.exception))
        save.assert_not_called()

    def test_refresh_reports_rotation_without_logging_tokens(self):
        class Response:
            status_code = 200

            def json(self):
                return {"access_token": "secret-access", "refresh_token": "secret-next"}

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, json):
                return Response()

        with patch.object(kpler_handler, "load_tokens", return_value={"refresh_token": "secret-previous"}), \
             patch.object(kpler_handler, "save_tokens") as saved, \
             patch.object(kpler_handler.httpx, "Client", Client), \
             self.assertLogs(kpler_handler.__name__, level="INFO") as logs:
            kpler_handler.KplerSession().ensure_access_token()

        saved.assert_called_once_with({"refresh_token": "secret-next"})
        message = " ".join(logs.output)
        self.assertIn("returned=True changed=True", message)
        for value in ("secret-previous", "secret-next", "secret-access"):
            self.assertNotIn(value, message)

    def test_rotated_token_persists_in_configured_state_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "kpler_tokens.json"
            with patch.object(kpler_handler, "TOKEN_FILE", destination):
                kpler_handler.save_tokens({"refresh_token": "next-token"})
                self.assertEqual(kpler_handler.load_tokens(), {"refresh_token": "next-token"})
            self.assertEqual(json.loads(destination.read_text()), {"refresh_token": "next-token"})

    def test_uses_configured_client_id_and_never_displays_response_secrets(self):
        calls = []

        class Response:
            status_code = 401

            def json(self):
                return {"error": "invalid_client", "error_description": "secret-from-server"}

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, json):
                calls.append(json)
                return Response()

        with patch.dict("os.environ", {"KPLER_CLIENT_ID": "authorized-client"}), \
             patch.object(kpler_handler, "load_tokens", return_value={"refresh_token": "private-token"}), \
             patch.object(kpler_handler.httpx, "Client", Client):
            with self.assertRaises(kpler_handler.KplerAuthenticationRequired) as failure:
                kpler_handler.KplerSession().ensure_access_token()

        self.assertEqual(calls[0]["client_id"], "authorized-client")
        self.assertEqual(calls[0]["refresh_token"], "private-token")
        self.assertIn("invalid_client", str(failure.exception))
        self.assertNotIn("secret-from-server", str(failure.exception))
        self.assertNotIn("private-token", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
