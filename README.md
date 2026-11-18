# SHOUT LAN — a network you can hear

Two laptops exchange messages **through sound**: speaker -> air -> microphone. No Wi-Fi, no Bluetooth, no cable. Each bit is a short beep (3000 Hz = 0, 5000 Hz = 1, 50 bits a second) after a chirp that marks the start of a frame.

On top of that sound link SHOUT LAN implements real networking mechanisms, so the things that are invisible on Wi-Fi become audible and visible:

- framing, addressing and a CRC-16 checksum;
- acknowledgements, timeouts and retransmission (Stop-and-Wait, Selective Repeat);
- carrier sense (CSMA) and ALOHA medium access;
- **Noise Shield**: forward error correction (Hamming(7,4) with interleaving) that repairs damaged bits without a retransmission;
- discovery, routing and multi-hop relay with TTL; acoustic ping; AES-256-GCM encryption.

A browser dashboard shows every frame as it travels, arrives damaged, is resent or is repaired.

## Start

| | |
|---|---|
| **Mac (node 1)** | double-click `start_mac.command` |
| **Windows (node 2)** | double-click `start_windows.bat` |

Each starts the engine on that laptop and opens its dashboard (`http://127.0.0.1:8081` on the Mac, `http://127.0.0.1:8082` on Windows). Nothing needs a network: the dashboard is served from the laptop to its own browser.

The demo walk-through, with what to say and what to do if something fails, is in **`docs/DEMO_SCRIPT.md`**.

From a terminal:

```
python shout.py --node 1 --peer 2       # real sound: this laptop's speaker + microphone
python shout.py --node 1 --pair         # one laptop: nodes 1 and 2 share its speaker + mic (real sound)
python shout.py --node 1 --simulated    # no sound: nodes 1 and 2 on a SIMULATED software air
```

Options: `--http PORT`, `--port PORT` (TCP control link, default 5000), `--mic N`, `--speaker N`, `--shared`, `--volume V`, `--f0 HZ`, `--f1 HZ`, `--key FILE`, `--no-browser`. Stop with Ctrl+C.

## Install (once per laptop)

Python 3.13.

```
macOS:    python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
Windows:  py -3.13 -m venv .venv
          .venv\Scripts\python -m pip install -r requirements.txt
```

The start files do this by themselves on the first run. The web app is committed pre-built in `web/dist/`, so Node is only needed to change the dashboard (`cd web && npm install && npm run build`).

## Before using sound

1. Close every other program that plays sound. On Windows the engine opens the speaker and microphone in WASAPI **exclusive** mode, because the normal shared mode runs noise suppression that removes the beeps.
2. Set the volume and do not change it while frames are in the air.
3. Keep the laptops 30-50 cm apart, speakers facing each other, and do not move them once it works.
4. The two tone frequencies have to suit the two laptops and the room. The default 3000 / 5000 Hz was measured between a MacBook and a Windows laptop; `tools/tone_trial.py` measures it again (see `docs/DEMO_SCRIPT.md`).

## What the two demo controls are

- **Noise Shield** — off: a plain frame; any flipped bit fails the checksum and the frame is resent. On: the frame carries spare bits (3 for every 4) and the receiver repairs up to one flipped bit in every 7. It costs about 1.75 times the airtime, and it does not beat strong steady noise or noise that hides the start chirp.
- **Generated noise** — the laptop really plays noise through its speaker (clicks or hiss) so the demo is repeatable. It is always labelled as generated.

## Tests

```
python -m pytest -q          # silent: unit, protocol, simulated air, fake audio, web bridge
```

The tools under `tools/` play sound: `loopback_test.py`, `frame_test.py`, `arq_test.py`, `shield_test.py`, `tone_trial.py`.

What was measured, on which machines, and what is still unverified: **`docs/TESTING.md`**.

## Project layout

```
shout.py                   launcher: engine + dashboard server
start_mac.command  start_windows.bat
shout_lan/   config, modem, fec, audio_io, channel, receiver, frame, crc, arq, mac, engine,
             control_server, web_bridge, node, security, stats, calibration, aloha_sim
web/         the dashboard (React + TypeScript); built output in web/dist/
tools/       sound tests, tone trial, key generator
tests/       pytest suite
ns2/         NS-2 topology script + AWK analysis (separate simulation module, not run)
docs/        DEMO_SCRIPT, DASHBOARD_API, TESTING, ARCHITECTURE, SYLLABUS_MAPPING, THREAT_MODEL, SCOPE
results/     saved measurements
```
