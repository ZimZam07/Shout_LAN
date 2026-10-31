# SHOUT LAN — Testing Evidence Log

Each entry records what was run, on which machine and Python version, and the actual result. Milestones in `MILESTONES.md` are ticked only from entries here.

Test machine (development): Windows 11 Home 10.0.26200, Realtek speaker, Intel Smart Sound microphone array.

---

## M0 — Environment setup (2026-10-31)

*Environment only; no application functionality is covered by this entry.*

| Check | Command / method | Result |
|-------|------------------|--------|
| Git repository exists | `git status --short` | Repo initialised; untracked: `.gitignore`, `MILESTONES.md`, `course_material/`, `docs/`, `requirements.txt` (nothing committed yet) |
| Course files preserved | `ls course_material` (moved with `mv`, not modified) | 8 files present |
| Primary interpreter | `.venv/Scripts/python --version` | Python 3.13.4 |
| Dependencies installed in venv | `pip list` | cryptography 50.0.2, numpy 2.5.4, PySide6 6.12.0, pytest 9.2.0, scipy 1.18.1, sounddevice 0.5.6 |
| Audio devices visible | `sounddevice.query_devices()` | WASAPI speaker (Realtek) and mic array (Intel) found, both default to 48 kHz |
| NS-2 availability (OPTIONAL) | `apt-cache policy ns2 nam` in WSL Ubuntu 26.04 | ns2 2.35 and nam 1.15 available but **not installed** (needs the user's sudo password) |

Python version status: 3.13 = primary (venv) · 3.14 = installed, not yet tested with the project · 3.11/3.12 = **untested** (not installed).

---

## M1 — Real acoustic link (2026-10-31)

**Kind of evidence:**

- *Measured – acoustic, single-laptop loopback*: this laptop's own speaker → air → its own microphone.
- *Not yet proven:* two different laptops. That requires the second laptop and is tracked separately below.

Python 3.13.4 (`.venv`).

### Unit tests (synthetic audio, no hardware) — `tests/test_modem.py`
`pytest` → **6 passed**. These cover:

- the bits↔bytes round trip;
- Goertzel picking the correct tone;
- a clean round trip with an unknown delay;
- a noisy round trip (≈ 6 dB per-sample SNR, ≤ 2 errors in 256 bits);
- no false preamble in 5 s of white noise;
- no false preamble inside plain BFSK data tones.

### Key hardware finding: Windows microphone processing destroys steady tones

| Mic path (same 256 random bits, 2000/3000 Hz, 20 ms bits) | Result |
|---|---|
| MME default (dev 1, shared) | 108/256 errors |
| DirectSound (dev 5, shared) | 106/256 errors |
| WASAPI shared (dev 9) | 93/256 errors |
| **WASAPI exclusive (dev 9)** | **0/256 errors** |
| WDM-KS array (dev 18–20) | cannot open (`Invalid device`) |

In shared mode, received tone power fell 20–40 dB during runs of identical bits; the noise suppressor removes sustained tones. **Fix:** `audio_io.Microphone` opens the mic with `sd.WasapiSettings(exclusive=True)` by default. Repeat comparison with `loopback_test.py --shared`: **568/1280 errors (BER 44.4 %)**, `results/loopback_20261031_171814.json`.

### Loopback BER trials — `tools/loopback_test.py` (mic 9 WASAPI exclusive, speaker 8)

| Run | Settings | Preambles | Bit errors | BER | Result file |
|---|---|---|---|---|---|
| 1 | 2000/3000 Hz, 20 ms, vol 0.5, 10×256 bits | 10/10 | 0/2560 | 0.00 % | `loopback_20261031_171613.json` |
| 2 | 2750/4750 Hz (sweep suggestion), 10×256 | 10/10 | 60/2560 | 2.34 % | `loopback_20261031_171724.json` |
| 3 | 2000/3000 Hz, vol **0.2**, 10×256 | 10/10 | 0/2560 | 0.00 % | `loopback_20261031_171925.json` |
| 4 | 2000/3000 Hz, vol 0.5, **20**×256, seed 11 | **20/20** | 7/5120 | **0.14 %** | `loopback_20261031_172254.json` |
| 5 | 2000/3000 Hz, **10 ms bits (100 bit/s)**, 5×256 | 5/5 | 4/1280 | 0.31 % | `loopback_20261031_172317.json` |

### False detections — `loopback_test.py --noise 60`
60 s of normal room noise: **0 false preamble detections**. Best correlation score 0.04 vs threshold 0.35 (`noise_20261031_172027.json`).

### Calibration — `loopback_test.py --calibrate`
- Noise floor RMS ≈ 0.002.
- The per-tone SNR sweep (1–6 kHz) shows 12–42 dB, with notches that move between runs because of room echoes.
- **Finding:** the sweep's "best SNR" pair (2750/4750) performed *worse* in real BER tests than 2000/3000.
- **Fix:** calibration now uses the sweep only to shortlist candidates and then ranks them by a real BER test. Second run (`calibration_20261031_172431.json`): 2000/3000 → 0/384, 1750/2750 → 0/384, 3000/5000 → 5/384, 2500/3500 → 72/384. **Recommended 2000/3000.**

### DoD status after M1
| DoD | Criterion | Status |
|---|---|---|
| B1 | Calibration measures noise floor, per-tone level, SNR | **Verified (loopback)** |
| B2 | ≥ 19/20 preambles detected, 0 false detections in 60 s | **Verified (loopback)**: 20/20 and 0 |
| B3 | 256-bit pattern BER ≤ 1 % at calibrated setting | **Verified (loopback)**: 0.00 %, 0.00 %, 0.14 % |
| — | Two-laptop acoustic verification | **Pending second laptop** (instructions in README) |

---

## M2 — Framing, addressing, CRC-16, parity, fragmentation (2026-10-31)

### Unit tests — `tests/test_frame.py`, `tests/test_modem.py` → **31 passed** (Python 3.13.4)

**CRC (DoD C2):**

- `crc16(b"123456789") == 0x29B1` ✔
- **Every** single-bit error in a 26-byte frame is rejected.
- **Every** double-bit error is rejected (exhaustive, about 41 000 combinations).
- Every burst of length 1–16 bits at every position is rejected.

**Parity vs CRC (DoD C3):** a 2-bit error passes the parity check but fails the CRC; a 1-bit error fails parity. ✔

**Frames (DoD C1):**

- All fields round-trip.
- A full frame is **35 bytes** (11 B overhead + 24 B payload); an ACK is 11 bytes.
- Malformed input (empty, too short, wrong sync, impossible length, truncated) raises `FrameError`, never a crash.
- An oversized payload is refused when building.

**Fragmentation (DoD C4):** a 50-byte message → 3 fragments (FIRST … LAST) → reassembled exactly. An empty message → 1 fragment. FIRST discards an abandoned partial message.

**Streaming receiver:**

- Decodes 3 frames from 2048-sample chunks.
- A corrupted frame is reported as `CRC mismatch`, not delivered.
- Chunk sizes 256/333/1000/2048/4799 all work.

**Modem:**

- The scrambler is reversible and limits runs of 192 zero bits to ≤ 8.
- The equaliser decodes correctly when tone 1 is ~15 dB weaker, including a lone 1 between two runs of 40 zeros.

### Hardware — `tools/frame_test.py` (single-laptop loopback, mic continuously open as in the real engine)

| Run | Change | HELLO frames valid | All frames | 3-fragment message | File |
|---|---|---|---|---|---|
| 1 | first version | 3/10 | 4/13 (9 CRC failures) | ✗ | `frames_20261031_172750.json` |
| 2 | same, raw audio saved | 3/10 | 4/13 | ✗ | `frames_20261031_173036.json` |
| 3 | **scrambler + equaliser + late window** | **10/10** | **13/13** | ✔ | `frames_20261031_173425.json` |
| 4 | same, volume 0.3 | **10/10** | **13/13** | ✔ | `frames_20261031_173528.json` |

**Root cause of runs 1–2** (from analysing the saved recording):

- In that session the 3000 Hz tone arrived about 12 dB weaker than 2000 Hz.
- Headers contain many 0x00/0x01 bytes, i.e. long 2000 Hz runs whose room echo spilled into the next 20 ms bit (inter-symbol interference).
- A lone 1-bit after a run of 0s therefore lost to the echo. CRC correctly rejected every damaged frame; nothing corrupted was delivered.
- The receiver code was ruled out: offline decoding of a clean continuous recording in chunk sizes 2048 to 400000 gave 3/3 valid frames.

**Fixes (all standard techniques):**

1. **Scrambler:** frame bytes are XOR-ed with the IEEE 802.11 LFSR sequence (x⁷+x⁴+1), so no long single-tone runs remain.
2. **Channel equalisation:** each tone's power is divided by its gain, measured from the known preamble.
3. **Later sampling window:** each bit is measured from 40–95 % of its slot instead of 15–95 %, skipping the previous bit's echo.

On the saved bad recording, (2)+(3) alone raised valid frames from 4/13 to 10/13. Adding (1) on live hardware gave 13/13.

**Also fixed:** `find_preamble` now waits until the whole peak-search region has been received. This prevents a too-early peak when a chunk ends inside a preamble. The synthetic regression test did not reproduce the original failure, so this fix is **preventive**, not proven necessary.

**Re-check of M1 after changing the bit window:** `loopback_20261031_173638.json` gave 0/2560 bit errors, 10/10 preambles.

### DoD status after M2
| DoD | Status |
|---|---|
| B4 "HELLO" frame valid ≥ 9/10 | **Verified (loopback)** 10/10 twice; two-laptop pending |
| C1 build/parse, malformed rejected | **Verified** (unit tests) |
| C2 CRC check value + error detection | **Verified** (unit tests) |
| C3 parity vs CRC | **Verified** (unit test); UI demo pending M4 |
| C4 3-frame reassembly | **Verified** (unit test + acoustic loopback) |

---

## M3 — ACK, timeout, retransmission, Stop-and-Wait (2026-10-31)

### Code review before testing
- **Speaker:** a single persistent `sd.OutputStream`. No `sd.play()` calls remain anywhere (`grep`). Per-frame overhead fell from ~1.1 s to ~0.47 s (`frames_20261031_173830.json`, 7/7 valid).
- **Engine threads:**
  - rx: channel → `FrameReceiver` → handler;
  - tx: `PriorityQueue`, so ACKs go before data;
  - sender: outbox → ARQ.
- The ARQ timer starts only after a frame has finished playing. ACKs reach the waiting sender through `ack_queue`; stale ACKs are ignored.
- **Initial sequence number** is random, so a restarted sender isn't mistaken for a duplicate.
- **Timer:** TCP-style RTO = SRTT + 4·RTTVAR, using Karn's rule.

### Simulated-channel tests — `tests/test_engine.py` → 8 passed (*Measured – SIMULATED channel*; protocol logic only)
Covers normal delivery, a 3-fragment message, a corrupted frame, a lost ACK delivered exactly once, max retries (5 retransmissions, 6 timeouts, FAILED), ping, HELLO discovery (lists only nodes that answered), and frames for other nodes being ignored. Full suite: **39 passed**.

### Real acoustic tests — `tools/arq_test.py` (*Measured – acoustic, single-laptop loopback*)
Two engines (nodes 1 and 2) share this laptop's speaker and microphone through `AcousticLoopbackHub`. Every DATA, ACK, PING and PONG frame travelled speaker → air → microphone.

| # | Scenario | Result | Key evidence | File |
|---|---|---|---|---|
| 1 | Normal transmission | **PASS** | delivered "HELLO WORLD", 0 retransmissions, 6.6 s | `arq_20261031_180122.json` |
| 2 | Corrupted DATA frame (injected) | **PASS** | receiver: `CRC mismatch (received 0x9570, calculated 0x9396)`; sender: timeout after 6.3 s, 1 retransmission; delivered once | same |
| 3 | Lost ACK (injected at receiver) | **PASS** | sender timed out and resent; receiver: "Duplicate seq 43 … ACKed again, not delivered twice"; message delivered **exactly once** | same |
| 4 | 3-frame message (56 B) | **PASS** | 3/3 frames ACKed, reassembled exactly, 21.8 s | `arq_20261031_180312.json` |
| 5 | Acoustic ping ×3 | **PASS** | 3 sent, 3 received, 0 % loss, RTT 4.68/4.70/4.71 s | same |
| 6 | Max retries (node 9 absent) | **PASS** | 6 attempts, 6 timeouts, 5 retransmissions → "Message #2 FAILED"; no false success | same |

Measured data-frame RTT (start of DATA → ACK received): 5.29–8.64 s; RTO converged to 4.46 s.

**Loopback-only artefact:** in scenario 2, node 1 also logged a CRC failure for its own corrupted frame, because both nodes share one microphone. On two laptops the half-duplex mute stops a node decoding its own transmissions.

**Interrupted run:** the first attempt (17:42) was stopped by the user before it finished and saved no results. It was rerun from the beginning as above.

### DoD status after M3
| DoD | Status |
|---|---|
| C5 lost ACK → resent, re-ACKed, delivered exactly once | **Verified (acoustic loopback + simulated)** |
| C6 corrupted frame → CRC failure shown, timeout, retransmit, delivered | **Verified (acoustic loopback + simulated)** |
| C7 max retries → "delivery failed" | **Verified (acoustic loopback + simulated)** |
| C8 20-char message ≥ 9/10 between **two laptops** | **Pending second laptop** |
| C13 ping RTT min/avg/max/loss | **Engine verified (acoustic loopback)**; UI display pending M4 |

---

## M4 — Engine, local TCP control link, desktop app (2026-10-31)

### Control link — `tests/test_control.py` → 8 passed
| DoD | Test | Result |
|---|---|---|
| A3a | `LineBuffer`: one message fed 1 byte per `recv()`; 3 messages in one chunk; a 3-byte UTF-8 character split across `recv()` calls | **pass**. Found and fixed a bug: `json.dumps` escaped non-ASCII by default, so real UTF-8 never went on the wire; now `ensure_ascii=False` |
| A3b | 2 clients, 4 threads × 1000 concurrent broadcasts → each client receives all 4000 lines, every line valid JSON, per-thread order preserved | **pass** |
| A3c | Code check: exactly one `.sendall(` in the server, inside `ClientHandler.writer` | **pass** |
| A3d | One client disconnects mid-stream → the other receives all 200 following events; client count drops to 1 | **pass** |
| A2 | Command reaches handler, reply goes to that client; `connect()` to a closed port raises the real OS error | **pass** |

### Engine processes — `tests/test_processes.py` → 2 passed (*SIMULATED channel*, UDP software air)
- Two `run_engine.py` processes plus a UI-like client and a second "monitor" client.
- The message was delivered; both clients received the events; `control_clients == 2`; status labelled SIMULATED.
- A bad command (`dst 999`) returns an `error` event and the engine keeps running.

**Bug found:** orphaned engine processes held their ports after a failed test start. **Fixed:** the fixture now always terminates the processes it started, and `run_engine.py` reports channel errors as a single `ERROR:` line.

### Stress + clean shutdown — `tests/test_stress.py` → 1 passed (*SIMULATED*)
- Setup: 2 engines, 4 TCP clients polling status as fast as possible, 3 messages each way at the same time.
- Result: all 6 delivered, all 6 received exactly once.
- Every client got only valid JSON, more than 50 status replies, and no error events (D3).
- Afterwards all 6 engine threads stopped and both ports could be bound again (D4).
- My first version of the D4 assertion was trivially true (`stop()` clears the thread list). It now captures the threads before stopping.

### Desktop app — `tools/ui_smoke_test.py` (real window, offscreen Qt, *SIMULATED* channel) → **20/20** (`ui_smoke_20261031_182823.json`)
- **Main flow:** Start engine → TCP connect; SIMULATED label in the top bar; HELLO discovery finds node 2; message shows "Delivered" only after the ACK; injected corruption → retransmission → delivered; monitor rows; measured metrics labelled SIMULATED; Stop engine → UI shows disconnected.
- **Responsiveness (D1):** longest Qt event-loop stall while a 3-frame message was transmitted = **4 ms**.
- **Every control has a real effect (A4):** volume slider (engine volume becomes 0.30), Apply tones, calibration disabled on SIMULATED, Refresh devices, Ping button (summary arrives), Drop-next-ACK (engine flag armed), Clear table, Reset (counters 0), Disconnect/Connect (A2 reconnect).
- **Screenshots** in `results/screenshots/` were reviewed. Problems found and fixed:
  - nav labels showed "Connect _Audio" (`&` treated as a shortcut marker);
  - broken spin-box/combo arrows;
  - the HTML chat rendered a full-width bar (replaced by bubble widgets);
  - stat tiles were clipped and overlapping;
  - the topology label was crossed by the connecting line;
  - the mic meter showed a level on the SIMULATED channel (now "no microphone (SIMULATED)").
- **Simulated-air realism fix:** the simulated channel delivered audio at the *start* of a transmission, so a reply could arrive while the sender was still "transmitting". It now delivers after the airtime, like sound. Simulated frame RTT is now 5.8 s, matching the acoustic 5.3–8.6 s.

### Error handling (A5) — `run_engine.py` with bad devices (no sound played)
| Arguments | Output | Exit |
|---|---|---|
| `--mic 999` | `ERROR: Audio device 999 does not exist` | 2 |
| `--speaker 999` | `ERROR: Audio device 999 does not exist` | 2 |
| `--mic 3` (a speaker) | `ERROR: Device 3 (Speaker (Realtek(R) Audio)) has no input channels` | 2 |
| `--speaker 9` (a mic) | `ERROR: Device 9 (Microphone Array …) has no output channels` | 2 |

**Bug found:** an invalid index first produced a raw `PortAudioError` traceback. **Fixed** with `audio_io.check_device()`.

### Real engine process on the acoustic link — `tools/engine_acoustic_check.py` (*Measured – acoustic*, one engine)
- `run_engine.py --node 1` (WASAPI exclusive) controlled over TCP.
- Calibration through the engine's own mic stream: OK.
- HELLO transmitted (1.91 s of sound).
- Status: channel `acoustic`, stats source "Measured – acoustic", 0 mic overflows; shutdown command → exit code 0.
- Files: `engine_acoustic_20261031_182206.json`, `…182246.json`.

**Bug found (SNR):** the first run reported 70.6 dB. Noise power had been computed over a 1 s block while tones used 90 ms blocks; Goertzel noise power depends on block length.
- **Fixed:** `calibration.noise_powers()` now uses equal-length blocks. A new unit test checks the SNR of a known synthetic signal to within 3.5 dB.
- After the fix: 63.1 dB / 65.5 dB **per ~18 Hz detector bin** (wideband SNR ≈ 31 dB). This is physically consistent: tone ≈ 0.07 amplitude, noise RMS 0.002.
- The UI now labels it "SNR inside the detector's ~18 Hz bin".
- The M1 sweep SNR figures above used the old method (and included the mic start-up transient). Only their relative ordering was used, and the tone choice was made by real BER tests, so the M1 conclusions stand.

### Not yet possible on one laptop
A full **two-process acoustic** run (UI on node 1 ↔ engine on node 2) cannot be done on one laptop: the microphone can be opened by only one process in exclusive mode. The same `Engine` class *was* verified over real sound in-process (M3, `arq_test.py`). Full app-to-app acoustic testing needs the second laptop.

### DoD status after M4
| DoD | Status |
|---|---|
| A1 starts without errors | **Verified** (engine sim + acoustic, UI offscreen) |
| A2 connect / real errors / reconnect | **Verified** (automated) |
| A3, A3a–A3d | **Verified** (automated) |
| A4 every control real | **Verified** (20/20 UI automation) |
| A5 missing device error | **Verified** |
| A6 no unhandled exceptions in full demo | Pending demo rehearsal (M10) |
| A7 both paths labelled | **Verified** (screenshots) |
| C13 ping in UI | **Verified (SIMULATED)**; acoustic engine ping verified in M3 |
| C14 HELLO discovery lists only real nodes | **Verified (engine test + UI, SIMULATED)**; acoustic two-laptop pending |
| D1 UI responsive | **Verified** (4 ms max stall) |
| D2 capture keeps running | **Verified (acoustic)**: 0 overflows in every acoustic run |
| D3 stress | **Verified (SIMULATED)** |
| D4 clean shutdown | **Verified** |
