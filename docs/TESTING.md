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

---

## M5 — Security: AES-256-GCM (2026-10-31)

### Unit tests — `tests/test_security.py` → 11 passed
| DoD | Check | Result |
|---|---|---|
| E1 | Round trip; 12-byte text → 40 bytes (+28); plaintext not visible in the blob | pass |
| E1 | One flipped bit in the **nonce**, **ciphertext** or **tag** → `authentication failed` | pass (3 cases) |
| E1 | Same blob claimed from another **origin** or for another **destination** → rejected (associated data) | pass |
| E1 | Wrong key → rejected | pass |
| E1a | 10 000 encryptions of the same text → 10 000 distinct nonces and ciphertexts; exactly one `os.urandom(NONCE_LEN)` in the module; `encrypt()` does not use `seq` | pass |
| E1b | The same blob decrypted twice → second attempt `replay detected` | pass |
| E1b limit | After 1000 newer messages the oldest nonce is forgotten and would be accepted again. This is the documented replay-cache limitation, and the test *asserts* it, so the docs can't silently drift | pass |
| E4 | Key file: 32-byte round trip; a wrong-length file → clear error; `secret.key` listed in `.gitignore` | pass |

### Engine tests on the SIMULATED channel — `tests/test_engine.py` (3 new, 11 total pass)
- Encrypted message delivered: `message_rx.encrypted == True`.
- The payload hex in **every** received DATA frame does *not* contain the plaintext, and every DATA frame carries the `ENC` flag.
- Wrong key at the receiver → the link layer still ACKs, but the security layer rejects (`security_reject: authentication failed`) and **no message is shown**.
- Receiver with no key → `security_reject: no key`.
- The sender refuses `encrypt=True` when it has no key.

### Real acoustic measurement — `tools/arq_test.py --security` (*Measured – acoustic, single-laptop loopback*) → 2/2, `arq_20261031_183229.json`
| "SECRET HELLO" (12 B) | Payload on air | Data frames | Delivered in |
|---|---|---|---|
| plain | 12 B | 1 | **6.8 s** |
| AES-256-GCM | 40 B | 2 | **14.5 s** |

The receiver logged "delivered (AES-GCM verified + decrypted): 'SECRET HELLO'". Overhead: 28 B = 4.48 s of pure airtime; here it also crossed the 24-byte frame limit, giving **+7.7 s (2.1×)** in total (E3).

**Bug found in that run:** the "queued" event said "1 frame(s)" for the encrypted message, because frames were counted before encryption. **Fixed:** it now counts the encrypted size ("12 bytes, 40 after encryption, 2 frame(s)").

### Key generation
`tools/gen_key.py` created `secret.key` (32 bytes) in the project folder. `git check-ignore` confirms it is ignored (`.gitignore:5`), and `git log --all -- secret.key` shows it has never been committed. It refuses to overwrite an existing key without `--force`.

### UI — `tools/ui_smoke_test.py` → **24/24** (`ui_smoke_20261031_184*.json`, *SIMULATED*)
New checks:
- the Encrypt checkbox is enabled when a key is loaded;
- the encrypted message is delivered and marked 🔒;
- every `ENC` frame in the monitor carries no plaintext;
- the Notes column labels them "🔒 ENC: payload is AES-GCM ciphertext" (screenshot `6_monitor_encrypted.png`).
- Rejected encrypted messages appear in the chat as "⚠ Encrypted message rejected - not shown", with the reason.

**Intermittent UI stall (open):**
- One run (`ui_smoke_20261031_184653.json`) failed the responsiveness check: the longest Qt event-loop stall was **1363 ms**.
- Timing instrumentation was then added around every UI event/status handler (logs any handler > 50 ms).
- Three reruns: longest stall 24–26 ms, **no** slow handler, 24/24 each.
- The cause of the one 1.36 s stall is **not identified and not reproduced**. Because no handler was slow afterwards, the likeliest (unconfirmed) explanation is OS scheduling while two engine processes were decoding audio. The instrumentation stays in the test so any repeat is pinpointed.

### Threat model
`docs/THREAT_MODEL.md` covers:
- eavesdropping (metadata stays visible);
- tampering (CRC is not security; the GCM tag is);
- replay (cache limits);
- outsider vs **insider** spoofing (the shared-key limitation);
- unencrypted frames;
- jamming;
- forged ACKs;
- key theft;
- nonce reuse;
- remote control access;

plus the measured cost.

### DoD status after M5
| DoD | Status |
|---|---|
| E1, E1a, E1b | **Verified** (unit tests) |
| E2 ciphertext on air, plaintext only after verification | **Verified** (acoustic loopback + simulated + UI) |
| E3 overhead measured | **Verified** (acoustic loopback: +7.7 s, 2.1× for 12 B) |
| E4 key ignored by git, missing/wrong key errors | **Verified** |
| E5 threat model | **Done** |

---

## M6 — Selective Repeat, CSMA, ALOHA (IN PROGRESS, 2026-10-31)

### Automated tests — `tests/test_sr_mac.py` → 14 passed (unit + *SIMULATED* channel); full suite 86 passed
| DoD | Check | Result |
|---|---|---|
| C9a | Receiver buffers 0, 2, 3, then 1 and delivers 0, 1, 2, 3; the SACK before the missing frame = (11, bitmap 0b110, cumulative) | pass |
| C9d | A resent copy of a delivered frame and of a buffered frame → `duplicates = 2`, nothing delivered twice; sequence wrap 254, 0, 1, 255 → 254, 255, 0, 1 | pass |
| — | A new message after an abandoned one is not stuck; the sender reads the SACK bitmap correctly | pass |
| C9 | 84-byte message (4 frames) with frame 2 lost (injected): exactly that frame is resent; receiver logs "buffered out of order" | pass |
| C9b | SACK lost (injected): sender times out, resends only the oldest frame, receiver reports a duplicate, message delivered exactly once | pass |
| C9c | POLL frame lost: recovery via timeout, delivered | pass |
| — | Selective Repeat faster than Stop-and-Wait for the same 4-frame message | pass |
| C10 | CSMA (simulated): node 1 defers while node 2 occupies the channel; both messages delivered | pass |
| C11 | Pure ALOHA: 5 random backoffs before 5 retransmissions, no carrier sensing | pass |
| C12 | Pure ALOHA simulation at G=0.5 and slotted at G=1.0 within ±0.03 of theory (20 000 frame-times); maxima near G 0.5 / 1.0 | pass |

**Bug fixed during M6:** the simulated channels marked the medium "busy" only at the *end* of a transmission (a side effect of the M4 realism change), which is backwards for carrier sensing. Both simulated channels now mark it busy for the whole transmission. The UDP version sends a short "carrier" notice first.

### Hardware runs — `tools/arq_test.py --m6` (*Measured – acoustic, single-laptop loopback*)
| Run | Notes | Result |
|---|---|---|
| `arq_20261031_191159` | CSMA threshold 10 dB | 1/4 by the original criteria. About half of all frames had CRC errors. Test bug: the CSMA scenario matched message ids across nodes and stopped early |
| `arq_20261031_191725` | threshold 20 dB, node-aware wait | 2/4 by the original criteria. Every message was delivered exactly once. The "failures" were the criterion "exactly N retransmissions", which can't hold on a noisy channel |
| `arq_20261031_192459` (+ raw audio) | **principled criterion**: SR must never resend a frame a SACK already acknowledged | **4/4** |
| `arq_20261031_193213` (+ raw audio) | larger audio buffers (`latency="high"`) | **4/4** |

Across these hardware runs, **no Selective Repeat retransmission was ever of an already-acknowledged frame** (26 retransmissions checked in the first two runs, 0 violations; later runs check it automatically).

### Investigation: frames corrupted from a point onwards
- **Symptom:** a corrupted frame is correct up to byte k, then wrong to the end.
- **Ruled out:**
  - speaker underflows / mic overflows: 0 reported (counters added to every `frame_tx`);
  - sound-card clock drift: ≤ 2 samples over 5.6 s (measured offline);
  - outside noise: corrupted frames had *less* energy at unused frequencies (median −0.3 dB) than good ones (+1.1 dB);
  - low confidence: many corrupted frames had *no* low-confidence bits.
- **Found** (offline correlation against the transmitted waveform): sudden timing **jumps of ≈192 / ≈384 samples (4 / 8 ms, one Windows audio period)** inside frames. A brief level dip at one jump suggests a playback gap.
- **Mitigation 1:** larger stream buffers (`latency="high"`). Bad frames fell from 20/52 to 8/36 between runs (not a controlled comparison).
- **Mitigation 2:** symbol-timing recovery in `Modem.demodulate_bits` (sliding-DFT running sums; alignment re-checked every 4 bits).
  - Synthetic tests with mid-frame gaps of ±192/±384 samples and double gaps: all pass; the same gaps without tracking break the frame.
  - Tuned by re-decoding the 89 real recorded frames: alignment window 30–100 % of the slot, threshold 0.03 → **72/89 valid vs 60/89 without tracking**.

**Status of timing recovery: UNDER EVALUATION, not verified.** Re-checking M1/M2 on hardware afterwards gave:
- `loopback_20261031_194412`: one trial with 114/256 confident errors (others 0).
- `frames_20261031_194503`: 9/13 valid.
- `loopback_20261031_194808` + raw audio: BER 20 %.
  - Offline analysis of that run: **the received signal level dropped about 4× from trial 4 onwards** (≈0.025 → ≈0.006 RMS), while the mic noise level is unchanged when measured separately. Most likely the playback volume changed externally during the run.
  - Errors occurred with tracking OFF too (9 bad trials) and ON (10 bad trials); tracking fixed 2 trials and broke 2.
  - **This run cannot judge the tracker.** A controlled A/B under stable volume is pending.

### M6 hardware session 2 (2026-10-31, Windows volume fixed at 70 % by the user)
*Measured – acoustic, single-laptop loopback.* Context: during the earlier `loopback_20261031_194808` run the user had lowered the Windows volume to 30 % mid-run, which explains that run's 4× level drop.

| Run | Result | Finding |
|---|---|---|
| `arq_20261031_203726` (+ raw audio) | **0/4**; even headers corrupted | Levels normal (RMS ≈ 0.027, no clipping) |
| Offline A/B on that audio: timing recovery OFF vs ON | **25/44 vs 3/44** valid frames | On the two older recordings: 28/36 vs 30/36 and 32/53 vs 42/53. **Decision: timing recovery is unreliable → OFF by default** (`Modem.TRACKING_DEFAULT = False`); the code is kept as experimental, with its unit tests run with it explicitly enabled |
| `loopback_20261031_2039xx` at transmit volume 0.5 / 0.3 / 0.15 | BER 10.9 % / 8.6 % / 5.9 % | Much worse than in M1 |
| `calibration_20261031_204155` (shared playback) | 2000/3000 Hz 73/384 errors; 2500/3500 Hz **0/384** | Default tones changed to 2500/3500 Hz (`config.py`) |
| Mic-only room check (15 s, nothing played) | no loud moments; steady hum only below 2.2 kHz | Room interference ruled out |
| Re-analysis of the `bits_20261031_194808` failures | timing jumps of **800–1200 samples** inside trials (earlier runs with small buffers: 192–384) | **The size of the jump scales with the audio buffer size → whole playback buffers are being lost** |
| **Interleaved A/B, 16 trials:** speaker shared/exclusive × buffers low/high | shared+low **18.6 %**, shared+high **4.4 %**, **exclusive+low 0 %, exclusive+high 0 %** (8 exclusive trials, 0/2048 bit errors) | **Root cause: Windows shared-mode playback drops buffers.** Fix: speaker opened in WASAPI exclusive mode (`audio_io.OUT_EXCLUSIVE = True`) |
| `calibration_20261031_205649` (exclusive playback) | all 4 candidate tone pairs 0/384 | Channel clean again with exclusive playback (this tool's printed SNR values are inflated: its noise sample catches post-open silence) |
| `arq_20261031_230338` | 0/3 — `Error opening OutputStream: Invalid device` | Exclusive mode was **refused** at that moment (another app was most likely using the speaker; a silent open test minutes later succeeded in both modes). **Fix:** the engine reserves the speaker at start-up and, if exclusive mode is refused, falls back to shared mode with a visible warning (`speaker_mode` in status and the Connect page) |
| `frame_test` (twice) | printed nothing | **Unexplained**; run with filtered output, so any error text may have been hidden. Investigate silently / in the next hardware session |
| `arq_20261031_231537` (persistent exclusive speaker, engine path) | **0/3** strict; many corrupted frames, including ACKs. Every message was still delivered exactly once (`HELLO WORLD`, `CRC TEST`, `ACK LOSS TEST`), but the senders reported FAILED after 5 retries in 2 of 3 cases | **OPEN:** the per-trial exclusive A/B was error-free, but the continuous engine path is not. Not resolved |
| `arq_20261031_231645` `--security` | 2/2 pass (plain 7.6 s; encrypted needed 5 data-frame transmissions, 52.4 s) | Same degraded channel |

**Status at the end of this session (the user then paused all audio work):** the acoustic link on this laptop is **not currently verified reliable** in the continuous engine path. One clear root cause was found and fixed (shared-mode playback dropping buffers), but a remaining cause in the persistent-stream engine path is unidentified. Next steps are in the hardware-test checklist (`docs/HARDWARE_TEST_PLAN.md`).


---

## Silent development phase (2026-10-31 night → 2026-11-01): no sound played

At the user's request, **no test in this phase used the speaker or microphone.** All evidence below is *Measured – SIMULATED channel*, *fake audio* or *unit test*.

### New: fake-audio harness — `tests/fake_audio.py`, `tests/test_acoustic_path_silent.py` (6 tests)
Fake speakers stream "played" samples in scaled real time into the fake microphones of every node, including their own (like a real laptop). This runs the **real acoustic code path**: `AcousticChannel`, half-duplex handling, carrier sensing with band energy, the speaker fallback, and `tools/frame_test.py`.

**Bugs found and fixed through it:**
1. **The post-transmission mute wasn't scaled** with the engine's time scale (test-only effect).
2. **Real design flaw, half-duplex:**
   - *Old design:* the receiver discarded microphone audio while transmitting and for 0.15 s afterwards.
   - *What went wrong:* the other node's reply (SACK, or a resend right after a SACK) could start inside that window and lose its preamble, so the whole frame was missed and an extra timeout followed. This happened in about 1 of 3 runs.
   - *Why hardware tests missed it:* the single-laptop hardware tests use a shared microphone with muting switched off.
   - *Redesign:* the microphone is never switched off. Our own frames are ignored by source address; a corrupted frame whose preamble was detected while we were transmitting is treated as our own echo; and carrier sense ignores energy measured during and 0.3 s after our own transmission.
   - *Result:* **5/5 consecutive runs of all 6 tests pass** (previously intermittent).
3. **`tools/frame_test.py` runs correctly** on fake audio (13/13 frames, 3-fragment message reassembled). The two silent hardware runs of this tool remain unexplained; the tool logic itself is fine.

### M7 — discovery, routing, multi-hop — `tests/test_routing.py` (6 tests, *SIMULATED* with a range model: 1 – 2 – 3)
| DoD | Check | Result |
|---|---|---|
| — | 1 → 3 with no route (out of range) → honestly FAILED, nothing delivered | pass |
| C15 | Route 3 via 2: delivered, origin 1, `hops = 2`; node 2 relays but shows no message; every DATA frame at node 3 has link src 2, origin 1, final 3, TTL 3 | pass |
| C15 | 84-byte message relayed with Selective Repeat on both hops | pass |
| C15 | Routing loop (1 → 2 → 1 …) to a non-existent node: dropped by TTL after exactly 3 forwards | pass |
| E2 | Encrypted message relayed: node 2 never decrypts (no `message_rx`, no `security_reject`), only ciphertext on air; node 3 decrypts | pass |
| C14 | Discovery: node 1 lists only node 2; node 2 lists 1 and 3 | pass |

**UI end-to-end with 3 engine processes** (node 3 started with `--hears 2`): the route is added through the Overview controls; node 3's own control link reports `message_rx` with origin 1 and 2 hops. The sender shows "✓ Handed to relay node 2 (hop ACK; end-to-end not confirmed)", an honest label because there is no end-to-end ACK.

### M8 — NS-2 (OPTIONAL / PENDING) — `ns2/`
- `shoutlan_topology.tcl` (lab-style: 4 nodes, bottleneck relay link, TCP/FTP + UDP/CBR, trace + nam) is **written but NOT RUN**: NS-2 isn't installed (needs the user's sudo password). **No simulation results exist.**
- `analysis.awk` is verified against a **hand-written** 15-line trace with hand-computed answers (`tests/test_ns2_awk.py`): flow 1 → 2 packets, 2080 B, 166.40 kbit/s, 50.00 ms; flow 2 → 80.00 ms, 1 drop.

### UI fixes found by reviewing screenshots
- **Chat bubbles cut long messages** to their first line (Qt wrapped-label height). Fixed by giving each label an explicit width and height.
- **Topology:** the relayed node was drawn off-screen and overlapping. Replaced with a left-to-right chain: this laptop → heard neighbours → nodes reached via a relay (dashed orange), with a legend.

### UI automation — `tools/ui_smoke_test.py` → **32/32** (final run on the finished code: `ui_smoke_20261101_005933.json`)
- New checks: route shown in the table; node 3 (out of range) receives the relayed message; honest relay status.
- One run (`ui_smoke_20261101_002903`) failed 2 checks (relay within 120 s, ping summary within 40 s). Both passed on the immediate rerun (`…003250`), and no UI handler was slow. These were real-time limits on simulated exchanges, so the limits were widened to 180 s / 90 s.

### Full silent suite
| Python | Result |
|---|---|
| 3.13.4 (`.venv`) | **99 passed** |
| 3.14.2 (`.venv314`, system packages + cryptography/pytest) | **99 passed** |
| 3.11 / 3.12 | not installed → **untested** |

---

## Dashboard link work (2026-11-18, MacBook Air, macOS): preamble fix, Noise Shield, interference generator

Volume is the macOS output volume, read at the start and end of every run (`audio_io.system_output_volume`). Runs where it changed during the run were discarded. Transmit volume 0.5, tones 2500/3500 Hz, 50 bit/s, mic 0 / speaker 1 (built-in).
Labels: **[air 60 %]** real sound at demo volume, **[air 22 %]** real sound at the quiet development volume, **[offline]** decoding saved real recordings (noise mixed in digitally where stated), **[sim]** SIMULATED channel / fake audio.

### Weak preamble detection on the MacBook: cause and fix
- **Symptom:** chirp detected in only 6/10 trials (scores 0.23-0.41, threshold 0.35).
- **Cause [offline]:** 94 % of the built-in mic's idle noise power is below 300 Hz (`room_noise_30s`: 93.6 % below 300 Hz, 0.2 % in 2-3 kHz). The received chirp was about as loud as that rumble in RMS, and the normalised cross-correlation divides by ALL the energy in the window, so the rumble halved every score. In-band SNR was 20-30 dB.
- **Fix:** band-pass 900-4500 Hz (4th-order Butterworth, `config.DETECT_BAND`) on the microphone audio before the chirp search, done chunk by chunk with filter state (`receiver.py`). The filter's 12-sample delay is measured on the clean chirp and removed from the timing.
- **Same recordings, old vs new detector [offline]:** `bits_20261118_175449` 6/10 → 10/10 detected; `bits_..._180703` 2/20 → 20/20; `bits_..._181154` (22 %) 1/6 → 6/6. True-frame scores went from 0.23-0.41 to 0.53-0.58.
- **False detections [offline]:** 0 in 30 s of room noise, 30 s of music played in the room and 30 s of speech (`say`). Best false scores: 0.15 / 0.19 / 0.15 for both chirps (threshold 0.35); 0 in 10 s of generated band noise (unit test).

### Bit errors after detection: echo, not noise
- Most errors were a 3500 Hz bit right after a 2500 Hz bit: the 2500 Hz tone keeps ringing into the next slot. A band-pass before the Goertzel step changed nothing (235 vs 230 errors).
- **The preamble-based tone equaliser hurt here [offline]:** gain errors up to 32 dB (the chirp passes each tone for under 2 ms), 3× more bit errors. It is now tried only if the CRC fails and the tones look more than 10 dB apart.
- **New decision [offline, 80 trials / 20 480 bits]:** measure 50-98 % of each slot instead of 40-95 %, and subtract 15 % of the previous slot's tone power as echo. Bit errors 2.13 % → 0.66 %.

### Plain link, end result
| Run | Result |
|---|---|
| `loopback_20261118_185152` **[air 60 %]** | **20/20 detected, BER 5/5120 = 0.098 %** |
| `arq_20261118_185257` (`--quick`, 2 engines, loopback hub) **[air 60 %]** | **3/3** (single run) |
| `loopback_20261118_183030` **[air 35 %]** | 10/10 detected, BER 0.98 % (the old decoder on the same audio: 2.0 %) |
| `arq_20261118_182816` **[air 22 %]** | 0/3: about 1 % bit errors at this volume break too many 176-bit frames and ACKs |
| `loopback_20261118_183744` **[air 60 %]** | DISCARDED: volume changed 60 → 44 during the run, and there were loud room sounds (peaks above full scale) |

**Lowest reliable volume:** at 22-35 % the plain link runs at about 1 % BER, which is not reliable. At 60 % it is clean. One run hung inside the audio library (no progress for 7 min) and was killed; the immediate rerun was fine. Cause unknown.

### Noise Shield as built
- Hamming(7,4) with soft-decision decoding. Interleaved per block: the header block (bytes 0-5, 12 codewords) and the body block, each sent column by column.
- Format signalled by a down-chirp (plain frames use the up-chirp); the receiver decodes both automatically.
- Replies (ACK, SACK, PONG, HELLO answer) go out in the format of the frame they answer.
- Airtime: a 16-byte frame is 2.67 s plain vs 4.59 s shielded; a 35-byte frame is 5.71 s vs 9.91 s.

### Interference generator
- Band-limited Gaussian noise (2-4 kHz). Level 1.0 = the RMS of our tones at transmit volume 0.5.
- While the generator is on, one mixer thread owns the speaker stream and writes 1024-sample blocks back to back. Frames are added at consecutive sample positions, so their timing cannot jump.
- Mixer timing **[air 15 %]:** 286 writes, longest interval between writes 27 ms, samples written = wall time, 0 underflow flags.
- A frame sent through the mixer with the generator on was decoded on air.
- SIMULATED air: generator noise ×5 (`SIM_NOISE_SCALE`), because the software air has no echo. Plain frames failed at level ~1.5 in software vs ~0.3 through real sound.

### Shield OFF vs ON under generated noise (16-byte DATA frame)
**[offline]:** real frames recorded at 22 % (`frames22_a`, 12 per format) with the generator's own recorded noise mixed in. 48 decodes per point. About 4 of each row's "missed" plain frames are a recording artefact (one truncated recording).

| level | plain OK | plain CRC/header reject | plain missed | shield OK | bits repaired |
|---|---|---|---|---|---|
| 0.20 | 33/48 | 11 | 4 | 48/48 | 20 |
| 0.25 | 27/48 | 15 | 6 | **48/48** | 43 |
| 0.30 | 34/48 | 7 | 7 | 47/48 | 50 |
| 0.35 | 25/48 | 15 | 8 | 43/48 | 64 |
| 0.40 | 22/48 | 10 | 16 | 42/48 | 73 |

**[air 60 %]** `shield_20261118_185556`, level 0.25, 20 frames per format, alternating:

| Format | OK | Rejected | Not detected | Bits repaired |
|---|---|---|---|---|
| plain | 15/20 | 5 | 0 | – |
| shield | 19/20 | 1 (two errors in one header codeword) | 0 | 1 |

0 underflows. Adding more recorded generator noise to that same 60 % recording **[offline]**: level 0.4 → plain 13/20, shield 18/20; level 0.5 → plain 10/20, shield 17/20.

**Speech as the interferer [offline]:** recorded speech mixed in at the same overall loudness as level-0.3 generator noise: plain 19/24, shield 23/24. All plain losses were missed preambles, not CRC failures. Louder speech beat both formats by hiding the preamble. The shield does little against speech, which is mostly below 2 kHz.

**Default level: 0.25.**

### Clicks: impulsive generated noise (2026-11-18, OFFLINE MIXING ONLY, no sound played)
Purpose: a generated noise that plain frames almost never survive, while the Noise Shield repairs it. It is chosen to suit forward error correction with interleaving, and is presented that way.

**Method [offline]**
- Synthetic click trains (`audio_io.ClickGenerator`) were mixed into REAL loopback recordings of random bits: `bits_20261118_185152` at 60 % and `bits_20261118_181012` at 50 %. 40 recordings, 3 click phases each, so 120 decodes per row.
- Before mixing, each click train was passed through a channel response measured from that recording's own preamble (Wiener deconvolution), so it also gets the room echo.
- Plain frame: OK only if every bit of the frame decodes right.
- Shielded frame: decoded with the receiver's soft Hamming decoder (with erasures) per codeword, using that frame size's interleaver geometry. For a linear code the decision does not depend on which codeword was sent.
- A missed preamble counts as a failure for both formats.

**Design steps (all offline)**
- **Per-burst flip probability:** a burst that knows nothing about the data flips a bit at most about half the time, because FSK compares the two tones and the burst lands on the sent tone half the time. One 18 ms full-scale single-tone burst flipped at least one bit in 48 % of cases.
- **Burst span:** with the room echo and the echo canceller, a burst damaged 1-4 adjacent bits.
- **Burst kind:** single-tone bursts beat two-tone and narrow-band-noise bursts (0.48 vs about 0.35 vs about 0.03 per burst).
- **Interval geometry:** the interleaver sends codeword `t mod n` at on-air bit t (n = 12 for the header; 10, 12, 20, 32, 58 for the bodies of 11-, 12-, 16-, 22- and 35-byte frames). With an even interval, bursts tile the codewords in pairs. Among 22-29 bit times, 26 (520 ms) gave the best shield result:

  | Interval (bit times) | Shielded DATA 16 B OK (of 80) |
  |---|---|
  | 23 | 60 |
  | 25 | 66 |
  | 26 | 73 |
  | 27 | 61 |
  | 29 | 72 |

  Shorter intervals (11-17) broke the shield (at most 32/40 shielded decodes OK).
- **Erasures (new, shield receiver):** a bit slot more than 4× louder than the frame's median is marked erased, and the decoder ignores it. A Hamming codeword can fill in 2 erased bits.
  - Effect at the chosen setting: shielded DATA 91 → 103 of 120.
  - With no generated noise: 120/120 both with and without erasures.
  - Hiss at level 0.25 on real recorded frames: shield still 48/48.
- **Echo-canceller cap: rejected.** Capping the canceller so a burst cannot spill into the next bit raised the clean-channel errors from 124 to 175-208 of 22 016 bits.

**Chosen setting:** `CLICK_LEN` 864 samples (18 ms), `CLICK_INTERVAL` 26 bits (520 ms), level 0.8 (burst peak 0.8 of full scale), erasure factor 4.

**Result: 120 decodes per cell [offline]**

| Frame | Plain OK | Plain failed (rejected or missed) | Shielded OK first time |
|---|---|---|---|
| ACK, 11 bytes | 22 | **82 %** | **106 (88 %)** |
| DATA, 16 bytes | 10 | **92 %** | **103 (86 %)** |

7 of 120 decodes per row (6 %) lost the preamble to a burst, for both formats.

**Same setting, real receiver, real recorded DATA frames [offline]:**
- Full `FrameReceiver` (detection, header, soft decoding with erasures, CRC) on the 22 % recordings `frames22_a`, 4 click phases each.
- Plain: 4/44 OK, 40 rejected by CRC (91 % failed).
- Shield: 45/48 OK (94 %), 3 preambles missed, 235 bits repaired, 208 erased.

**Exchange estimate:**
- Plain DATA+ACK per attempt: (10/120)·(22/120) ≈ 1.5 %, so about 9 % of messages are delivered within the 6 attempts.
- Shielded exchange first time: about 76 %; within 6 attempts it is practically always delivered.

**Targets:**
- Plain ≥ 90 % failed: met for DATA (92 %), not quite for ACK (82 %).
- Shield ≥ 90 % OK first time: 86-88 % on the 50-60 % recordings, 94 % on the real-receiver DATA check.
- Raising the level or shortening the interval makes plain fail more, but shield collisions and preamble misses grow faster (level 1.0: shield 75 %).

**Simulated air [sim]:** clicks are not scaled. Plain ACK 5/40 OK, DATA 1/40; shield 40/40 for both.

**Not verified:** clicks on real sound. The coordinator does the on-air confirmation.

**Per-transmission shield [sim] (`test_shield_switched_on_while_a_plain_message_is_stuck_gets_it_through`):**
- The engine now reads the shield setting when each frame actually goes on air, not when it was queued.
- Before this change, a retry waiting in carrier sense kept the old format.
- The test: a message is stuck as plain frames under clicks, the shield is switched on at the first timeout, every later DATA transmission is shielded, and the message is delivered.
