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

- [x] **M1 Acoustic link** – tone TX, calibration, preamble detection, BFSK bit recovery (DoD B1–B3). Verified on single-laptop loopback (TESTING.md §M1); two-laptop check pending second laptop
- [x] **M2 Framing + CRC** – frame format (11 B overhead), addressing, CRC-16, parity, fragmentation, scrambler + equaliser (B4, C1–C4). Verified: unit tests + loopback 13/13 frames (TESTING.md §M2); two-laptop pending
- [ ] **M3 Stop-and-Wait** – ACK, timeout, retransmit, duplicates, error injection (C5–C8)
- [ ] **M4 App** – engine threads, TCP control server (per-client writer queue), line-buffered client, PySide6 UI (5 screens), monitor, stats, ping, HELLO (A1–A7, A3a–A3d, C13, C14, D1–D4)
- [ ] **M5 Security** – AES-GCM, random nonces, replay check, overhead measurement, threat model (E1–E5)
- [ ] **M6 Protocols** – Selective Repeat incl. SACK loss, CSMA, ALOHA + simulation (C9–C9d, C10–C12)
- [ ] **M7 Relay** – routing table, TTL, 3-engine test (C15)
- [ ] **M8 NS-2** – **OPTIONAL / PENDING academic confirmation**; never blocks M1–M7 (G1–G3)
- [ ] **M9 4-FSK** – OPTIONAL, only after BFSK is stable
- [ ] **M10 Final** – docs, two-laptop test with you, final DoD statuses (F1–F4, H1–H7)
