# SHOUT LAN — Milestone Checklist

Legend:

- `[ ]` not started
- `[~]` in progress
- `[x]` done **with evidence recorded in `docs/TESTING.md`**
- `[!]` blocked
- `[-]` not required

**Evidence rule:** a box is ticked only when every DoD criterion listed for that milestone has test evidence in `docs/TESTING.md`. Creating files, folders or UI elements is never counted as completion.

**Gate rules:** M2 waits until M1 works on real hardware; M6 waits until M3 delivers messages reliably.

## Environment (not application progress)

- [x] **M0 Environment setup** – git repo, originals preserved in `course_material/`, Python 3.13 `.venv`, `requirements.txt` installed. Evidence: TESTING.md §M0. *This says nothing about application functionality.*

## Application milestones

- [x] **M1 Acoustic link** – tone TX, calibration, preamble detection, BFSK bit recovery (DoD B1–B3). Verified on single-laptop loopback (TESTING.md §M1). ⚠ Later sessions showed regressions traced to Windows shared-mode playback (fixed: exclusive speaker); **re-verification after the audio-path changes is pending** (HARDWARE_TEST_PLAN H1–H4); two-laptop pending
- [x] **M2 Framing + CRC** – frame format (11 B overhead), addressing, CRC-16, parity, fragmentation, scrambler + equaliser (B4, C1–C4). Verified: unit tests + loopback 13/13 (TESTING.md §M2). Hardware re-check pending (H2); two-laptop pending
- [x] **M3 Stop-and-Wait** – ACK, timeout, retransmit, duplicates, error injection (C5–C7). Verified: 6/6 scenarios over real sound (loopback) + 8 simulated tests (TESTING.md §M3). C8 two-laptop pending
- [x] **M4 App** – engine threads, TCP control server (per-client writer queue), line-buffered client, PySide6 UI (5 screens), monitor, stats, ping, HELLO (A1–A5, A7, A3a–A3d, C13, C14, D1–D4). Verified (TESTING.md §M4). A6 at demo rehearsal; app-to-app acoustic needs 2nd laptop
- [x] **M5 Security** – AES-GCM, random nonces, replay check, overhead measurement, threat model (E1–E5). Verified (TESTING.md §M5): 11 unit + 3 engine tests, acoustic overhead +7.7 s, 24/24 UI
- [x] **M6 Protocols** – Selective Repeat incl. SACK loss, CSMA, ALOHA + simulation (C9–C12). Verified: simulated + fake-audio tests, loopback `--m6` 4/4 twice (never resent an acknowledged frame), ALOHA simulation vs theory. ALOHA over sound and two-laptop runs pending; timing recovery kept **off** (experimental)
- [x] **M7 Discovery + relay** – routing table, store-and-forward relay, TTL, (origin, final) reassembly, encrypted relay, UI routing controls (C14, C15). Verified on the SIMULATED channel (6 tests + 3-process UI test). Real 3-laptop relay: **no third laptop available**
- [~] **M8 NS-2** – OPTIONAL / PENDING academic confirmation. Scripts written (`ns2/`), AWK verified on a hand-written trace; **simulation not run** (NS-2 not installed: needs your sudo password) — G1 Blocked
- [-] **M9 Optional extras** – 4-FSK, X25519 key exchange, file transfer: **not implemented** (optional; core reliability first). Symbol-timing recovery exists as an experimental, disabled feature
- [~] **M10 Final** – all silent work done: integration, 99/99 tests on 3.13 and 3.14, 32/32 UI automation, all docs, final DoD statuses. **Remaining: the real-hardware session and two-laptop demo rehearsal** (docs/HARDWARE_TEST_PLAN.md)
