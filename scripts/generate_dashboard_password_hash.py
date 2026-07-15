#!/usr/bin/env python3
"""Generate a bcrypt password hash without echoing the password."""
from __future__ import annotations

import getpass
import sys

import bcrypt


def main() -> int:
    password = getpass.getpass("Dashboard password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return 1
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    print(bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
