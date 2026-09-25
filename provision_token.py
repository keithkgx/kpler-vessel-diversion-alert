"""Install a new authorized Kpler refresh token without exposing it to shell history."""
import getpass
from kpler_handler import save_tokens

token = getpass.getpass("Fresh authorized Kpler refresh token: ").strip()
if not token:
    raise ValueError("A refresh token is required")
save_tokens({"refresh_token": token})
print("Token stored in kpler_tokens.json with owner-only permissions.")
