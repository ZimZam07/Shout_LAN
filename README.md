# SHOUT LAN — an acoustic computer network

Two (or more) Windows laptops exchange messages **through sound**: speaker → air → microphone.
On top of that sound link SHOUT LAN implements real networking mechanisms: framing, addressing,
CRC-16, ACKs, timeouts, retransmission (Stop-and-Wait), ping/RTT, discovery and more. A PySide6
desktop app shows everything as it happens.

> **Two communication paths, never mixed**
> - **Acoustic link (sound)** — between laptops. The only path that carries messages between laptops.
> - **Local control link (TCP, 127.0.0.1)** — between the app and the engine on the *same* laptop.

## 1. Install (once per laptop)

Requirements: Windows 10/11, Python **3.13** (primary, tested). Python 3.11–3.12 should work but are untested. A working speaker and microphone are also needed.

```powershell
cd Shout_LAN
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## 2. Run

### Desktop app (recommended)
```powershell
.venv\Scripts\python run_ui.py
```
1. **Connect & Audio** → choose a **Node ID** (laptop A = 1, laptop B = 2, …).
2. Keep **Acoustic link (sound)** selected, check the microphone/speaker (WASAPI entries are pre-selected), keep **Exclusive mic mode** on.
3. Press **Start engine**. The app starts the engine and connects to it automatically.
4. Optional: **Calibrate** to see the noise floor and tone SNR.
5. **Overview → Discover** sends a HELLO. Nodes that answer appear in the list.
6. **Messages** → choose the destination node and send. "Delivered" appears only after the receiver's ACK is heard.

### Engine only (command line)
```powershell
.venv\Scripts\python run_engine.py --node 1                  # real acoustic link
.venv\Scripts\python monitor_client.py                       # second TCP client: live event printout
```
Options: `--mic N --speaker N` (device numbers as listed in the app), `--volume 0.5`, `--port 5000`,
`--shared` (Windows' processed mic mode, not recommended), `--simulated` (backup mode, below).

### Two laptops
Run the app on both laptops with **different node IDs** and the **same tones** (default 2000/3000 Hz).
Place them 0.5–1 m apart, speakers facing each other, in a quiet room. Volume about 0.5.

### SIMULATED backup mode (no sound)
For a noisy room or testing on one laptop: select **SIMULATED channel** (or `--simulated`). Engines on the
**same laptop** exchange the modulated audio samples over local UDP instead of the air. Everything in the
app is then labelled **SIMULATED**, and it is never presented as acoustic communication. Use a different
port per engine, e.g. node 1 on port 5001 and node 2 on port 5002.

## 3. Diagnostics (these play sound)

| Tool | What it does |
|---|---|
| `tools/loopback_test.py` | Known random bits → speaker → mic; reports BER. `--calibrate`, `--noise 60`, `--shared` |
| `tools/frame_test.py` | Real frames incl. a 3-fragment message. `--listen 120` / `--send-only` for two laptops |
| `tools/arq_test.py` | Stop-and-Wait over real sound (two engines share this laptop's speaker+mic). `--quick`, `--rest` |
| `tools/engine_acoustic_check.py` | Real engine process controlled over TCP: calibrate + HELLO |
| `tools/ui_smoke_test.py` | Drives the real app offscreen on the SIMULATED channel (no sound) |

Unit and integration tests (no sound): `.venv\Scripts\python -m pytest -q`

## 4. Troubleshooting

| Problem | Fix |
|---|---|
| "Device N does not exist / has no input channels" | Press **Refresh devices** and pick a microphone (WASAPI). |
| Engine fails to start in exclusive mode | Another app is using the microphone; close it (Teams, Zoom, browser tabs). |
| Many "CRC mismatch" frames | Move the laptops closer, reduce room noise, run **Calibrate**, try volume 0.3–0.7. |
| Frames never detected | Check both laptops use the same tones; unmute the speaker; Windows volume ≥ 50 %. |
| Very poor reception with `--shared` | Expected: Windows noise suppression removes steady tones (see docs/TESTING.md §M1). |

## 5. Project layout

```
run_engine.py  run_ui.py  monitor_client.py
shout_lan/   modem, frame, crc, receiver, arq, engine, channel, control_server, ...
ui/          PySide6 app (5 screens)
tools/       hardware diagnostics
tests/       pytest suite
docs/        scope, definition of done, testing evidence
results/     saved measurements (JSON) and screenshots
course_material/  original course files (unchanged)
```
