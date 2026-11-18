#!/bin/bash
# SHOUT LAN - start this Mac as node 1. Double-click this file (or run it in Terminal).
# Works with Wi-Fi and Bluetooth off: everything runs on this laptop and the sound card.
cd "$(dirname "$0")"
pkill -f "shout.py --node" 2>/dev/null && sleep 2
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Python (needs internet once)..."
  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python shout.py --node 1 --peer 2
