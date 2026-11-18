# SHOUT LAN

Laptops exchange messages through sound (speaker to microphone). `shout.py` starts the engine and a browser dashboard on this laptop. Two-laptop demo: the Mac is node 1, the Windows laptop is node 2.

## "Start the server"

When asked to start the server (or start the node, start the demo):

1. Stop any node already running on this laptop (a `shout.py --node` process), and wait 2 seconds so the ports and the sound card are free.
2. Start it in the background and keep it running:
   - macOS: `.venv/bin/python shout.py --node 1 --peer 2`
   - Windows: `.venv\Scripts\python shout.py --node 2 --peer 1`
3. Show the `READY` line (or the `ERROR:` line) word for word, and the dashboard address: `http://127.0.0.1:8081` on the Mac, `http://127.0.0.1:8082` on Windows.
4. On Windows, fetch `http://127.0.0.1:8082/api/status` and report `speaker_mode`. It should be `exclusive`; `shared` means another program is using the sound card and should be closed, then start again.

Do not change code when asked only to start the server.

The same thing without Claude Code, and without any network: double-click `start_mac.command` on the Mac or `start_windows.bat` on Windows.

## Other modes

- `shout.py --node 1 --pair`: two nodes on ONE laptop sharing its speaker and microphone (real sound). Dashboards on 8081 and 8082.
- `shout.py --node 1 --simulated`: two nodes on one laptop with no sound, labelled SIMULATED.

## Before a demo

Close every other program that plays sound, set the system volume and do not change it while frames are in the air, and keep the laptops 30-50 cm apart with speakers facing each other.

## Working on the code

- Tests: `python -m pytest -q` (silent). The tools under `tools/` play sound.
- `docs/DASHBOARD_API.md` is the contract between the engine and the web app in `web/` (built output is committed in `web/dist/`).
- Commit messages are plain descriptions with no co-author lines.
