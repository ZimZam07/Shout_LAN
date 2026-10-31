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
