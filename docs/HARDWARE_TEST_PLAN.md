# SHOUT LAN — Remaining Real-Hardware Tests

Every test below **plays sound and/or uses the microphone**. They are deferred to a dedicated hardware session.

Everything that can be tested silently is already automated (`python -m pytest -q`, `tools/ui_smoke_test.py`).

## 0. Setup for every hardware session (do this first)
1. **Close or mute every other program that can play sound**: browser tabs, Teams/Zoom, music, video. SHOUT LAN opens the speaker in **WASAPI exclusive mode** and Windows refuses this while another app is playing (`arq_20261031_230338`).
2. **Windows volume fixed** (e.g. 70 %) and **not changed during a test**. Laptops on a stable surface; speaker and mic uncovered; quiet room.
3. Start the engine or app and check the Connect page shows **"speaker exclusive"**. "Speaker in SHARED mode" means reliability will be poor (shared-mode playback dropped whole buffers: 18.6 % BER vs 0 %).
4. Run **Calibrate** (or `python tools/loopback_test.py --calibrate`) and use the recommended tones **on both laptops** (default 2500/3500 Hz).

Record every result in `docs/TESTING.md` and set the matching DoD status.

## Priority 1 — the open acoustic issue (single laptop)
| # | Test | Command | Pass criterion |
|---|---|---|---|
| H1 | Bit link with exclusive speaker, persistent stream | `python -u tools/loopback_test.py --trials 20 --save-audio` | BER ≤ 1 %, 20/20 preambles |
| H2 | Frame test (continuous mic + persistent speaker) | `python -u tools/frame_test.py --save-audio` | ≥ 12/13 valid, 3-fragment message reassembled. It printed nothing in two filtered runs; run it **unfiltered** and record any error |
| H3 | Engine-path ARQ (the failing case) | `python -u tools/arq_test.py --quick --save-audio` | 3/3. Last result 0/3 with many corrupted frames (`arq_20261031_231537`) |
| H4 | If H3 fails: offline slip analysis of the saved audio | scratch analysis as in TESTING.md §M6 | Find whether timing jumps remain. If so, try `audio_io.OUT_LATENCY = "low"`, then the old per-frame playback, then timing recovery (`Modem.TRACKING_DEFAULT = True`), comparing them **on the same saved audio** |

## M1 — Physical layer
- [ ] H1 above (BER ≤ 1 %); `--noise 60` → 0 false preambles (B2).
- [ ] Calibration result recorded; tones agreed for the demo room.

## M2 — Framing and CRC
- [ ] H2 above (B4: "HELLO" ≥ 9/10 valid).

## M3 — Stop-and-Wait
- [ ] `python -u tools/arq_test.py --quick` → normal, corrupted frame, lost ACK all pass (C5, C6).
- [ ] `python -u tools/arq_test.py --rest` → 3-frame message, ping ×3, max retries (C7, C13).

## M4 — Real engine + app on the acoustic link
- [ ] `python -u tools/engine_acoustic_check.py` → calibration OK, HELLO sent, speaker mode **exclusive**, exit 0.
- [ ] App: Start engine (acoustic) → mic meter moves, Calibrate draws the SNR chart, no "SHARED mode" warning.

## M5 — Security over sound
- [ ] `python -u tools/arq_test.py --security` → plain and encrypted both delivered; the encryption overhead is recorded (E2, E3).

## M6 — Selective Repeat, CSMA, ALOHA over sound
- [ ] `python -u tools/arq_test.py --m6 --save-audio` → 4/4. Criterion: delivered exactly once and **never resends an already-acknowledged frame**; the injected lost frame must be among those resent (C9–C10).
- [ ] App: set MAC = Pure ALOHA and send to an absent node → backoff events (C11).

## Two-laptop tests (need the second laptop) — the most important proof
Same setup on both laptops (section 0), same tones, node 1 and node 2, 0.5–1 m apart.

| # | Test | Pass criterion | DoD |
|---|---|---|---|
| T1 | Laptop B `python tools/frame_test.py --listen 120`; laptop A `python tools/frame_test.py --send-only` | ≥ 9/10 HELLO frames valid on B | B4 |
| T2 | App on both: Overview → Discover | each laptop lists the other, and only the other | C14 |
| T3 | Send a 20-character message 10 times A→B | ≥ 9/10 delivered | **C8** |
| T4 | Error injection: A "Corrupt next DATA frame", B "Drop next ACK" | CRC failure shown on B; timeout + retransmit on A; delivered exactly once | C5, C6 |
| T5 | Ping ×4 | RTT min/avg/max and loss shown | C13 |
| T6 | Selective Repeat + "Lose frame 2" | only frame 2 resent | C9 |
| T7 | CSMA: A sends a long message, B sends during it | B logs "channel BUSY … deferring" | C10 |
| T8 | Encrypted message (same `secret.key` on both) | delivered, 🔒 shown; monitor shows ciphertext | E2 |
| T9 | Laptop with a wrong/missing key | "Encrypted message rejected", not shown | E1 (live) |
| T10 | Full `docs/DEMO_SCRIPT.md` rehearsal | no unhandled exceptions in either engine console | A6 |
| T11 | Fresh clone + `pip install -r requirements.txt` + README steps on laptop B | app starts | H5 |

## Three-laptop test (only if a third laptop is available)
- [ ] A–B–C in a line, with A and C out of hearing range of each other (different rooms / far apart). A: route "3 via 2". Message A→C arrives at C with "2 hops" (C15 real).
