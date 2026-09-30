"""One-time setup: sign in with Google in the browser; saves the refresh token into .env.

    uv run --env-file .env python -m assistant.modules.calendar.auth

Needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET set (see modules/calendar/README.md).
"""

import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from assistant.modules.calendar.client import SCOPES

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    SCOPES,
)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

env = Path(".env")
lines = [
    ln
    for ln in env.read_text(encoding="utf-8").splitlines()
    if not ln.startswith("GOOGLE_REFRESH_TOKEN=")
]
env.write_text(
    "\n".join([*lines, f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}"]) + "\n", encoding="utf-8"
)
print("Saved GOOGLE_REFRESH_TOKEN to .env")
