"""
gen_key.py - create the shared AES-256 key file `secret.key` (like Assignment 9).

    python tools/gen_key.py            # creates secret.key in the project folder

Copy the SAME file to every laptop (USB stick - never send it over the
acoustic link). The file is listed in .gitignore and must never be committed.
Make a new key for each demo / semester.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shout_lan.security import generate_key   # noqa: E402

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secret.key")

if os.path.exists(PATH) and "--force" not in sys.argv:
    print(f"{PATH} already exists. Use --force to replace it (all laptops then need the new file).")
    sys.exit(1)
generate_key(PATH)
print(f"Key generated successfully: {PATH}")
print("Copy this file to every laptop that should be able to read encrypted messages.")
