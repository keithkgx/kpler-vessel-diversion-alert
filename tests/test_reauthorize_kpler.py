import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import reauthorize_kpler


class Response:
    def __init__(self, status, data):
        self.status_code = status
        self.data = data

    def json(self):
        return self.data


class Client:
    responses = []
    calls = []

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def post(self, url, json):
        self.calls.append(("POST", url, json))
        return self.responses.pop(0)

    def get(self, url, headers):
        self.calls.append(("GET", url, headers))
        return self.responses.pop(0)


class ReauthorizationTests(unittest.TestCase):
    def setUp(self):
        Client.responses = []
        Client.calls = []
        self.http = patch.object(reauthorize_kpler.httpx, "Client", Client)
        self.save = patch.object(reauthorize_kpler, "save_tokens")
        self.http.start()
        self.saved = self.save.start()
        self.addCleanup(self.http.stop)
        self.addCleanup(self.save.stop)

    def assert_saved_session(self, refresh, access, client_id):
        self.saved.assert_called_once()
        saved = self.saved.call_args.args[0]
        self.assertEqual(saved["refresh_token"], refresh)
        self.assertEqual(saved["access_token"], access)
        self.assertEqual(saved["client_id"], client_id)
        self.assertGreater(datetime.fromisoformat(saved["expires_at"]), datetime.now(timezone.utc))
        self.assertNotIn("PASSWORD", saved)
        self.assertNotIn("EMAIL", saved)

    def test_password_login_saves_session_tokens_and_expiry(self):
        Client.responses = [Response(200, {"access_token": "access-secret",
                                           "refresh_token": "new-refresh-secret", "expires_in": 600})]
        reauthorize_kpler.login_and_save("operator@example.com", "private-password", "app-id")
        self.assertEqual(Client.calls[0][2]["grant_type"], "password")
        self.assertEqual(Client.calls[0][2]["client_id"], "app-id")
        self.assert_saved_session("new-refresh-secret", "access-secret", "app-id")

    def test_authenticator_mfa_uses_hidden_code_and_saves_session(self):
        Client.responses = [Response(403, {"error": "mfa_required", "mfa_token": "mfa-secret"}),
                            Response(200, {"access_token": "access", "refresh_token": "rotated"})]
        reauthorize_kpler.login_and_save("operator@example.com", "password", "app-id",
                                         prompt=lambda _: "1", secret_prompt=lambda _: "123456")
        self.assertEqual(Client.calls[1][2]["otp"], "123456")
        self.assert_saved_session("rotated", "access", "app-id")

    def test_email_mfa_uses_enrolled_authenticator(self):
        Client.responses = [
            Response(403, {"error": "mfa_required", "mfa_token": "mfa-secret"}),
            Response(200, [{"id": "totp|one", "active": True},
                           {"id": "email|two", "active": True, "oob_channel": "email"}]),
            Response(200, {"oob_code": "oob-secret"}),
            Response(200, {"access_token": "access", "refresh_token": "rotated"}),
        ]
        reauthorize_kpler.login_and_save("operator@example.com", "password", "app-id",
                                         prompt=lambda _: "2", secret_prompt=lambda _: "789012")
        self.assertEqual(Client.calls[2][2]["authenticator_id"], "email|two")
        self.assertEqual(Client.calls[3][2]["binding_code"], "789012")
        self.assert_saved_session("rotated", "access", "app-id")

    def test_rejected_login_does_not_overwrite_token_or_leak_response(self):
        Client.responses = [Response(401, {"error": "invalid_grant",
                                            "error_description": "private-response-secret"})]
        with self.assertRaises(reauthorize_kpler.KplerLoginError) as failure:
            reauthorize_kpler.login_and_save("operator@example.com", "private-password", "app-id")
        self.saved.assert_not_called()
        self.assertIn("HTTP 401", str(failure.exception))
        self.assertNotIn("private-password", str(failure.exception))
        self.assertNotIn("private-response-secret", str(failure.exception))

    def test_response_without_refresh_token_does_not_overwrite_existing_file(self):
        Client.responses = [Response(200, {"access_token": "access"})]
        with self.assertRaises(reauthorize_kpler.KplerLoginError):
            reauthorize_kpler.login_and_save("operator@example.com", "password", "app-id")
        self.saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
