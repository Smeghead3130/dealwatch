#!/usr/bin/env python3
"""Interactive, desktop-only token setup. Never prints or stores credentials in the repo."""

import argparse
import getpass
import logging
import os
import shlex
import tempfile
import uuid
from pathlib import Path


def write_credentials(email, token, destination, replace=False):
    repository = Path(__file__).resolve().parents[1]
    destination = destination.expanduser().resolve()
    if destination.is_relative_to(repository):
        raise ValueError("Credentials must remain outside the repository")
    if destination.exists() and not replace:
        raise FileExistsError("Credential file already exists")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".dealwatch-keep-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("KEEP_EMAIL=" + shlex.quote(email) + "\n")
            f.write("KEEP_MASTER_TOKEN=" + shlex.quote(token) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if replace:
            os.replace(temp, destination)
        else:
            os.link(temp, destination)  # Exclusive creation, including against concurrent writers.
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description="Save a dedicated Google Keep account token privately")
    parser.add_argument("--replace", action="store_true", help="Replace a previous credential file")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    destination = Path.home() / ".config" / "dealwatch-keep"
    if destination.exists() and not args.replace:
        print("Credentials already exist. Use --replace only to renew them.")
        return 1
    print("Use only the dedicated Dealwatch Google account, never your personal account.")
    print("1. Open a PRIVATE browser window: https://accounts.google.com/EmbeddedSetup")
    print("2. Sign in and accept. In F12 → Application/Storage → Cookies, copy oauth_token.")
    print("   The page may keep loading; that is okay. Do not paste the cookie into chat.")
    email = input("Dedicated account email: ").strip()
    if "@" not in email or "\n" in email or "\r" in email:
        print("Enter the dedicated account's email address.")
        return 1
    cookie = getpass.getpass("3. Paste oauth_token here (hidden): ").strip()
    if not cookie:
        print("No cookie supplied; nothing saved.")
        return 1
    try:
        import gpsoauth

        response = gpsoauth.exchange_token(email, cookie, f"{uuid.getnode():x}")
        token = response.get("Token")
        if not token:
            print("Google did not return a token. Repeat the sign-in with the dedicated account.")
            return 1
        write_credentials(email, token, destination, args.replace)
    except (Exception, KeyboardInterrupt):
        print("Setup failed; no credential details were printed. Retry with a fresh oauth_token.")
        return 1
    print("Saved privately to ~/.config/dealwatch-keep (mode 600). Close the private browser window.")
    print("Claude can load that file privately when deploying. Do not paste its contents into chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
