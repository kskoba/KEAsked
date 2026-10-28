#!/usr/bin/env python3
"""
One-time Google sign-in for the master-sheet integration (google_sheets.yaml
with `auth: oauth`). Opens a browser, you sign in as the Google account that
should own the monthly master sheets (e.g. the department's scheduling
mailbox), and the token is saved next to google_sheets.yaml. The backend
refreshes it on its own after that. Re-run only if the backend says the
sign-in is no longer valid.

Run from the repo root with the backend's Python:
    .venv/bin/python3 scripts/google-oauth-login.py

Headless backend (Unraid): run this on a desktop, then
    ./scripts/sync-config-to-unraid.sh
to copy google_oauth_token.json over.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler.backend import google_sheets_client as g  # noqa: E402


def main() -> int:
    cfg = g.load_google_sheets_config()
    if cfg is None:
        print("google_sheets.yaml not found -- copy google_sheets_template.yaml first.", file=sys.stderr)
        return 1
    if cfg.auth != "oauth":
        print(f"google_sheets.yaml has auth: {cfg.auth!r}; set `auth: oauth` to use a Google sign-in.", file=sys.stderr)
        return 1
    try:
        email = g.run_oauth_login(cfg)
    except g.GoogleSheetsApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Signed in as {email or '(unknown account)'}. Token saved to {g._in_config_dir(cfg.oauth_token_path)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
