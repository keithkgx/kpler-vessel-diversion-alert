import unittest
from unittest.mock import patch

import kpler_handler


class KplerAuthTests(unittest.TestCase):
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
