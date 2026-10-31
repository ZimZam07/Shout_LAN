# SHOUT LAN — Definition of Done

*Status: REVISION 2 for final approval (2026-10-31).*

A requirement is **DONE** only when it is **implemented, tested (evidence recorded in `docs/TESTING.md`) and documented**. Creating files, folders or UI elements never counts as evidence.

Final status values:

- **Verified complete**
- **Partially complete**
- **Blocked**
- **Not implemented**
- **Not required** (only for OPTIONAL / PENDING items you decide against)

"Test conditions" for acoustic criteria, unless stated otherwise:

- quiet room (normal conversation level or below);
- laptops 0.5–1 m apart, speakers facing each other;
- volume set by calibration;
- 50 bit/s (20 ms bits).

---

## A. Application

| # | Criterion | Evidence |
|---|-----------|----------|
| A1 | `run_engine.py` and `run_ui.py` start without errors on Python 3.13 (venv) | Startup log |
| A2 | The UI connects to the engine over TCP; a refused/closed connection shows the real error and the UI recovers on reconnect | Screenshot + test |
| A3 | Two clients (UI + `monitor_client.py`) are connected at once and both receive events | Log of both |
| A3a | **Line reassembly:** the shared `read_lines()` helper returns exactly the original messages when fed (i) one message split across several `recv()` chunks, (ii) several messages in one chunk, (iii) a message split mid-UTF-8 character | Unit test |
| A3b | **No interleaving:** with 2 clients connected and 4 engine threads broadcasting 1000 events each at the same time, every line each client receives is valid JSON, and each client gets all 4000 events in per-thread order | Stress test |
| A3c | Every client socket is written only by its own writer thread through its own outgoing queue (code review + test) | Test + review note |
| A3d | A client disconnecting mid-stream is removed cleanly; the other client keeps receiving | Test |
| A4 | Every button and control does something real; nothing is a placeholder | UI checklist |
| A5 | A missing/unplugged mic or speaker shows a clear error, not a crash | Test with a device disabled |
| A6 | No unhandled exceptions during the full demo script | Engine + UI logs |
| A7 | The two communication paths are labelled in the UI: "Local control link (TCP)" and "Acoustic link (sound)" | Screenshot |

## B. Acoustic communication (CORE)

| # | Criterion | Evidence |
|---|-----------|----------|
| B1 | Calibration measures the noise floor and the per-tone level through the real speaker → mic, and shows SNR | Calibration output |
| B2 | Preamble detection: ≥ 19/20 transmitted preambles detected, 0 false detections in 60 s of room noise (single-laptop loopback) | `loopback_test.py` report |
| B3 | Bit recovery: a 256-bit known pattern recovered with bit error rate ≤ 1 % in loopback at the calibrated setting | Report |
| B4 | A single frame with "HELLO" is received with a valid CRC in ≥ 9/10 trials (loopback, and two laptops) | Report + your two-laptop results |
| B5 | The node ignores its own transmissions (half-duplex handling) | Log |

## C. Networking

| # | Criterion | Class | Evidence |
|---|-----------|-------|----------|
| C1 | Frame build/parse round-trips all field values; malformed frames are rejected without crashing | CORE | Unit tests |
| C2 | CRC-16-CCITT gives `0x29B1` for `"123456789"` and detects 100 % of injected single-bit, double-bit and ≤ 16-bit burst errors in tests | CORE | Unit tests |
| C3 | Parity demo shows a 2-bit error that parity misses and CRC catches | SYLLABUS | Unit test + UI |
| C4 | A 3-frame message is reassembled in the correct order | CORE | Integration test |
| C5 | Stop-and-Wait: with "drop next ACK" the frame is resent, the receiver re-ACKs, the message is delivered **exactly once** | CORE | Test + monitor screenshot |
| C6 | Stop-and-Wait: with "corrupt next frame" the receiver shows the CRC failure, the sender times out, retransmits, and delivers | CORE | Test + screenshot |
| C7 | After max retries the sender reports "delivery failed" (no false success) | CORE | Test |
| C8 | A 20-character message is delivered correctly in ≥ 9/10 trials between two laptops | CORE | Your two-laptop results |
| C9 | Selective Repeat: with "drop frame N" only frame N is retransmitted (retransmission counter = 1) and the message is correct | SYLLABUS | Test + screenshot |
| C9a | SR out-of-order buffering: frames arriving as 0, 2, 3, then 1 are delivered to reassembly as 0, 1, 2, 3 | SYLLABUS | Unit test |
| C9b | SR **lost SACK**: with "drop next SACK", the sender times out, resends only the oldest unacked frame with POLL, receives a fresh SACK, and completes; the message is delivered **exactly once** | SYLLABUS | Unit test + simulated-channel test + screenshot |
| C9c | SR lost POLL frame: recovery via timeout, message correct | SYLLABUS | Unit test |
| C9d | SR duplicates: re-sent frames already buffered or delivered are reported in the SACK but never delivered twice; the sequence wrap-around 255 → 0 works | SYLLABUS | Unit test |
| C10 | CSMA: while another sound source plays in the tone band, the node logs "channel busy" and defers; it transmits once the channel is idle | SYLLABUS | Hardware test log |
| C11 | ALOHA (real): transmits without sensing, uses random backoff after a missing ACK | SYLLABUS | Log |
| C12 | ALOHA simulation: measured throughput is within ±0.03 of theory at G = 0.5 (pure) and G = 1.0 (slotted), with ≥ 10 000 slots; chart labelled "Simulation" | SYLLABUS | Unit test + screenshot |
| C13 | Acoustic ping reports per-reply RTT and a min/avg/max/loss % summary | SYLLABUS | Screenshot |
| C14 | HELLO discovery lists only nodes actually heard, with last-heard time | SYLLABUS | Screenshot |
| C15 | Relay A → B → C delivers using the routing table; TTL decrements; TTL 0 is dropped | SYLLABUS | 3-engine simulated test; real only with a 3rd laptop (otherwise marked *Partially complete*) |

## D. Multithreading

| # | Criterion | Evidence |
|---|-----------|----------|
| D1 | The UI stays responsive (window moves, buttons react) during a 30 s transmission | Manual test |
| D2 | Audio capture continues while a frame is being decoded (no dropped input blocks, reported by the audio callback status) | Log |
| D3 | Shared state is only reached through queues/locks; a stress test (concurrent sends + events + 2 clients for 2 min) shows no exceptions or corrupted counters | Test |
| D4 | The engine shuts down cleanly (all threads stop, ports released) | Test |

## E. Security

| # | Criterion | Evidence |
|---|-----------|----------|
| E1 | AES-256-GCM encrypt/decrypt round-trip; a modified ciphertext, tag, nonce, origin or destination is **rejected** | Unit tests |
| E1a | Nonces come from `os.urandom(12)`; encrypting the same plaintext 10 000 times gives 10 000 distinct nonces and ciphertexts; no code path derives a nonce from SEQ or a restartable counter | Unit test + code review |
| E1b | A replayed (previously accepted) encrypted message is rejected and logged as a replay | Unit test |
| E2 | Encrypted message delivered over the link; the monitor shows ciphertext in frames and plaintext only after verification | Screenshot |
| E3 | The extra air time from encryption (28 B = 4.48 s at 50 bit/s, plus any extra frames) is measured on the link and reported | Testing report |
| E4 | `secret.key` is git-ignored; a missing or wrong key shows a clear error | Test |
| E5 | `THREAT_MODEL.md` covers eavesdropping, tampering, replay, spoofing, jamming, key handling, nonce uniqueness, and the **shared-key limitation** (any keyholder can impersonate any node; a valid tag does not identify the sender) | Doc |

## F. Syllabus visibility

| # | Criterion |
|---|-----------|
| F1 | Each syllabus item in `SYLLABUS_MAPPING.md` has a "where in the app" and a "how to demonstrate" entry, each checked during the demo rehearsal |
| F2 | The protocol screen explains each protocol in ≤ 3 plain sentences |
| F3 | The packet monitor shows the layers of any frame (application → network → data link → bits) |
| F4 | Every metric is labelled with its source: *Measured – acoustic*, *Measured – SIMULATED channel*, or *Simulation* |

## G. NS-2 (separate module) — OPTIONAL / PENDING ACADEMIC REQUIREMENT

This applies only if you confirm the NS-2 deliverable is needed; otherwise G1–G3 are marked **Not required**. NS-2 work never blocks or delays sections A–F.

| # | Criterion |
|---|-----------|
| G1 | NS-2 runs in WSL (`ns` executes a script); if installation is impossible, this is documented as **Blocked** with the reason |
| G2 | At least one topology script (A–B–C relay with a bottleneck, TCP/FTP + UDP/CBR) runs and produces real `.tr` and `.nam` files |
| G3 | Throughput, delay and drop count are computed from the real trace by AWK (lab style) and committed with the outputs |

## H. Quality and delivery

| # | Criterion |
|---|-----------|
| H1 | `pytest` passes on **Python 3.13 (primary target)**; result recorded. Python 3.14 is run and reported separately. Python 3.11/3.12 are listed as **untested** unless an actual run is recorded |
| H2 | Simulated channel mode is labelled SIMULATED in the top bar, the logs and every metric |
| H3 | No fake statistics, statuses or devices anywhere (code review against this list) |
| H4 | Docs present: README (install + run), ARCHITECTURE, SYLLABUS_MAPPING, TESTING (report), DEMO_SCRIPT, DEFINITION_OF_DONE (final statuses), THREAT_MODEL, SCOPE |
| H7 | Air-time figures in the docs match the frame format (11 B overhead, 35 B / 5.6 s + preamble for a full frame) and are confirmed by measured airtime on the link |
| H5 | A fresh clone + `pip install -r requirements.txt` + README steps works on a second Windows laptop (your test) |
| H6 | Code comments explain which network concept each module/function implements |


---

# FINAL STATUS (end of the silent development phase, 2026-11-01)

Evidence key:
- **L** = real sound, single-laptop loopback;
- **S** = automated silent / SIMULATED / fake-audio tests (99 pytest tests on Python 3.13.4 and 3.14.2, 32-check UI automation);
- **pending** = needs a hardware session (`docs/HARDWARE_TEST_PLAN.md`).

Since the user paused all audio work, nothing new has been verified with sound. Hardware results from earlier sessions are kept as recorded.

| # | Status | Evidence / note |
|---|---|---|
| A1 starts without errors | **Verified complete** | S: engine (simulated + acoustic) and UI on 3.13 and 3.14; L: `engine_acoustic_*` |
| A2 TCP connect, real errors, reconnect | **Verified complete** | S: `test_control.py`, UI checks |
| A3, A3a–A3d control link | **Verified complete** | S: 8 control tests, stress test |
| A4 every control does something real | **Verified complete** | S: UI automation (32 checks incl. routing) |
| A5 missing device → clear error | **Verified complete** | 4 bad-device runs (no sound played) |
| A6 no unhandled exceptions in full demo | **Not implemented (not yet run)** | pending: two-laptop rehearsal (T10) |
| A7 both paths labelled | **Verified complete** | screenshots |
| B1 calibration | **Verified complete** | L: M1 + M6 calibration runs |
| B2 preamble detection / no false detections | **Partially complete** | L (M1): 20/20, 0 false in 60 s. Not re-run after the audio-path changes (H1) |
| B3 BER ≤ 1 % | **Partially complete** | L: 0.00–0.14 % in M1; 0 % in 8 exclusive-speaker A/B trials; degraded runs in M6 traced to shared-mode playback. Persistent engine path still open (H1–H4) |
| B4 HELLO frame ≥ 9/10 | **Partially complete** | L: 13/13 twice (M2), 7/13 later (shared playback). Two laptops pending (T1) |
| B5 node ignores its own transmissions | **Partially complete** | S: fake-audio half-duplex test (own frames and echo never reported); redesigned to keep the mic on. Real two-laptop check pending |
| C1 frame build/parse, malformed rejected | **Verified complete** | S |
| C2 CRC check value + error detection | **Verified complete** | S: exhaustive 1-bit, 2-bit and burst ≤ 16 |
| C3 parity vs CRC | **Verified complete** (unit test) | S. There is no separate UI button; explained in the docs |
| C4 3-frame reassembly | **Verified complete** | S + L (M2, M3) |
| C5 lost ACK → exactly-once | **Verified complete** (protocol) | L (M3 6/6) + S + fake-audio. Two laptops pending (T4) |
| C6 corrupted frame → CRC fail, retransmit | **Verified complete** (protocol) | L + S |
| C7 max retries → FAILED | **Verified complete** | L + S |
| C8 20-char message ≥ 9/10 between two laptops | **Blocked** | needs the second laptop (T3) |
| C9–C9d Selective Repeat | **Verified complete** (protocol) | S (sim + fake acoustic); L: `--m6` 4/4 twice; never resent an acknowledged frame. Two laptops pending (T6) |
| C10 CSMA defers when busy | **Verified complete** | L (loopback CSMA scenario passed 3×); S (sim + fake acoustic band energy) |
| C11 ALOHA backoff | **Partially complete** | S verified; not yet exercised over sound |
| C12 ALOHA simulation vs theory | **Verified complete** | S (±0.03 at G = 0.5 / 1.0); chart labelled "Simulation" |
| C13 acoustic ping | **Verified complete** | L: 3/3, RTT 4.68–4.71 s; S: UI |
| C14 discovery lists only real nodes | **Partially complete** | S (incl. range model); two laptops pending (T2) |
| C15 multi-hop relay, TTL | **Partially complete** | S: 3-engine tests, routing loop stopped by TTL, encrypted relay, UI end-to-end with 3 engine processes. Real 3-laptop test: no third laptop |
| D1 UI responsive | **Verified complete** | S: max stall 4–27 ms in normal runs. One unexplained 1.36 s stall, not reproduced since (TESTING §M5) |
| D2 capture keeps running | **Verified complete** | L: 0 mic overflows in every acoustic run |
| D3 thread-safety stress | **Verified complete** | S |
| D4 clean shutdown | **Verified complete** | S |
| E1, E1a, E1b crypto, nonces, replay | **Verified complete** | S |
| E2 ciphertext on air | **Verified complete** | L + S + UI |
| E3 overhead measured | **Verified complete** | L: 6.8 s → 14.5 s for 12 B |
| E4 key handling | **Verified complete** | S; `secret.key` never committed |
| E5 threat model | **Verified complete** | `docs/THREAT_MODEL.md` |
| F1 syllabus items demonstrable | **Partially complete** | `docs/SYLLABUS_MAPPING.md` + `DEMO_SCRIPT.md` written; rehearsal pending |
| F2 plain explanations | **Verified complete** | Protocols page |
| F3 layer view | **Verified complete** | Packet monitor detail |
| F4 metrics labelled by source | **Verified complete** | "Measured – acoustic / SIMULATED channel", "Simulation" |
| G1 NS-2 runs | **Blocked** (optional) | NS-2 not installed; needs the user's sudo in WSL |
| G2 topology script produces traces | **Partially complete** | `ns2/shoutlan_topology.tcl` written, **not run** |
| G3 AWK metrics from real traces | **Partially complete** | `analysis.awk` verified on a hand-written trace only |
| H1 tests pass on 3.13 (3.14 separately) | **Verified complete** | 99/99 on 3.13.4 and on 3.14.2. 3.11/3.12 **untested** |
| H2 SIMULATED labelled everywhere | **Verified complete** | top bar, metrics, logs, topology |
| H3 no fake data | **Verified complete** | code review; counters only from engine events |
| H4 docs present | **Verified complete** | README, ARCHITECTURE, SYLLABUS_MAPPING, TESTING, DEMO_SCRIPT, DoD, THREAT_MODEL, SCOPE, HARDWARE_TEST_PLAN |
| H5 fresh install on the second laptop | **Not implemented (not yet run)** | T11 |
| H6 code comments explain the concepts | **Verified complete** | review |
| H7 airtime figures match | **Verified complete** | 35-byte frame: airtime 5.75 s vs measured play duration 5.76 s (L) |
