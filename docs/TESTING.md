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
