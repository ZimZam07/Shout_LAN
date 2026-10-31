# SHOUT LAN — an acoustic computer network

Windows laptops exchange messages **through sound**: speaker → air → microphone, using BFSK tones at 50 bit/s.

On top of that sound link SHOUT LAN implements real networking mechanisms:
- framing, addressing, CRC-16;
- ACK / timeout / retransmission (Stop-and-Wait and Selective Repeat);
- CSMA and ALOHA medium access;
- discovery, routing and multi-hop relay with TTL;
- acoustic ping/RTT;
- AES-256-GCM encryption.

A PySide6 desktop app shows everything as it happens.

> **Two communication paths, never mixed**
> - **Acoustic link (sound):** between laptops. The *only* path that carries messages between laptops.
> - **Local control link (TCP, 127.0.0.1):** between the app and the engine on the *same* laptop.
>
> A **SIMULATED channel** exists for testing and as a demo backup. It is labelled SIMULATED everywhere.

Status of verification: see `docs/DEFINITION_OF_DONE.md` and `docs/HARDWARE_TEST_PLAN.md` (the real-hardware tests still to run).

## 1. Install (once per laptop)
Windows 10/11, **Python 3.13** (primary, tested). 3.14 was also tested (see TESTING.md); 3.11–3.12 are untested.

```powershell
cd Shout_LAN
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Encryption key, created once and then **copied by USB** to every laptop (never commit or send it):
```powershell
.venv\Scripts\python tools\gen_key.py
```

## 2. Before using sound (important)
1. **Close every other program that plays sound** (browser tabs, Teams, music). The engine opens the speaker and microphone in **WASAPI exclusive mode**; Windows' normal shared mode measurably breaks the link (dropped playback buffers, noise suppression on the mic). If exclusive mode is refused, the app shows **"Speaker in SHARED mode"**: close the other app and restart the engine.
2. Fix the Windows volume (e.g. 70 %) and don't change it while sending.
3. Run **Calibrate** in the room you will use, and set the **same tones on every laptop** (default 2500/3500 Hz):
   ```powershell
   .venv\Scripts\python tools\loopback_test.py --calibrate      # plays test tones
   ```

## 3. Run the app
```powershell
.venv\Scripts\python run_ui.py
```
1. **Connect & Audio:** Node ID (laptop A = 1, laptop B = 2, …); **Acoustic link (sound)**; keep the pre-selected WASAPI devices and Exclusive mic mode; **Start engine**. The app starts `run_engine.py` and connects to it over TCP.
2. **Overview → Discover:** nodes that answer appear (only nodes actually heard).
3. **Messages:** choose the destination and send. "✓ Delivered (ACK received)" appears only after the receiver's ACK is heard. Optionally tick **Encrypt (AES-GCM)**.
4. **Packet Monitor & Logs:** every frame with its layers; error-injection buttons.
5. **Protocols & Performance:** Stop-and-Wait / Selective Repeat; CSMA / Pure ALOHA / none; measured metrics; ALOHA simulation chart.
6. **Overview → Routing table:** "reach node 3 via node 2" for out-of-range nodes.

### Two laptops
Same setup on both, **different node IDs, same tones**, 0.5–1 m apart, speakers facing each other.

Full walkthrough: `docs/DEMO_SCRIPT.md`.

### Engine only (command line)
```powershell
.venv\Scripts\python run_engine.py --node 1                    # real acoustic link
.venv\Scripts\python monitor_client.py                         # second TCP client: live events
```
Options:

| Option | Meaning |
|---|---|
| `--mic N --speaker N` | device numbers as listed in the app |
| `--volume 0.5` | transmit volume |
| `--f0 2500 --f1 3500` | BFSK tones |
| `--port 5000` | local control port |
| `--route 3:2` | routing table entry (reach node 3 through node 2) |
| `--key secret.key` | encryption key file |
| `--shared` | Windows-processed mic mode, not recommended |
| `--simulated [--hears 2]` | SIMULATED backup mode (below) |

### SIMULATED backup / multi-node demo on ONE laptop (no sound)
```powershell
.venv\Scripts\python run_engine.py --node 1 --simulated --port 5001 --hears 2 --route 3:2
.venv\Scripts\python run_engine.py --node 2 --simulated --port 5002
.venv\Scripts\python run_engine.py --node 3 --simulated --port 5003 --hears 2
.venv\Scripts\python run_ui.py      # Connect to port 5001 (or Start engine with SIMULATED selected)
```
`--hears` limits which simulated nodes are "in range" (here 1 and 3 can only reach each other through 2). The modulated audio samples travel over local UDP; everything is labelled SIMULATED.

## 4. Tests

**Silent (no sound):**
```powershell
.venv\Scripts\python -m pytest -q                 # unit, protocol, simulated, fake-audio, control-link, NS-2 AWK
.venv\Scripts\python tools\ui_smoke_test.py       # drives the real window offscreen (SIMULATED channel)
```

**Hardware (play sound; see `docs/HARDWARE_TEST_PLAN.md`):**

| Tool | What it does |
|---|---|
| `tools/loopback_test.py` | known bits → speaker → mic, BER; `--calibrate`, `--noise 60`, `--save-audio` |
| `tools/frame_test.py` | real frames incl. a 3-fragment message; `--listen` / `--send-only` for two laptops |
| `tools/arq_test.py` | Stop-and-Wait / Selective Repeat / CSMA / security over real sound; `--quick --rest --security --m6 --save-audio` |
| `tools/engine_acoustic_check.py` | the real engine process over TCP: calibrate + HELLO |

## 5. Troubleshooting
| Problem | Fix |
|---|---|
| "Speaker in SHARED mode" / "exclusive mode refused" | Another program is using audio: close it, then Stop/Start engine |
| "Device N does not exist / has no input channels" | **Refresh devices**, pick the WASAPI microphone/speaker |
| Many "CRC mismatch" | Same tones on both laptops? Run Calibrate; move closer; quieter room; check speaker exclusive |
| Frames never detected | Tones differ between laptops, speaker muted, or Windows volume too low |
| Want to test without sound | SIMULATED channel (section 3) or the silent test suite |

## 6. Project layout
```
run_engine.py  run_ui.py  monitor_client.py   entry points
shout_lan/   config, modem, audio_io, channel, receiver, frame, crc, arq, mac, engine,
             control_server, linebuffer, node, security, stats, calibration, aloha_sim
ui/          PySide6 app (app.py, control_client.py, widgets.py, style.py, pages/)
tools/       hardware diagnostics, key generator, UI smoke test
tests/       pytest suite (incl. fake_audio.py for silent acoustic-path tests)
ns2/         NS-2 topology script + AWK analysis (separate simulation module)
docs/        SCOPE, DEFINITION_OF_DONE, TESTING, ARCHITECTURE, SYLLABUS_MAPPING,
             DEMO_SCRIPT, THREAT_MODEL, HARDWARE_TEST_PLAN
results/     saved measurements (JSON) and UI screenshots
course_material/  original course files (unchanged)
```
