# SHOUT LAN — Scope Document

*Status: REVISION 2 for final approval (2026-10-31). Nothing below is implemented yet.*

Changes in revision 2:

- frame overhead corrected to 11 bytes;
- AES-GCM nonce rules and shared-key limitations added;
- TCP event server write-safety and message buffering specified;
- Selective Repeat fully specified, including SACK loss;
- NS-2 made OPTIONAL / PENDING;
- Python support wording corrected;
- milestone evidence rule added.

## As implemented (2026-11-01): differences from the plan below, all driven by measurements
| Plan | Implementation | Why (evidence in TESTING.md) |
|---|---|---|
| Tones 2000/3000 Hz | Default **2500/3500 Hz**; **Calibrate** chooses tones by real bit-error tests | 2000/3000 fell into an echo notch after the laptop moved (19 % vs 0 % errors) |
| Shared-mode audio | **WASAPI exclusive** microphone *and* speaker; shared-mode fallback is shown as a warning | Shared mic: 44 % BER (noise suppression); shared speaker: 18.6 % BER (dropped buffers) vs 0 % exclusive |
| Receiver deaf for a while after transmitting | **Microphone never muted**; own frames and own echo filtered | The deaf window made quick replies lose their preamble (fake-audio tests) |
| — | Scrambler (802.11 LFSR) + preamble-based equaliser + late bit window | Echo-induced errors on real hardware (M2) |
| — | Symbol-timing recovery: implemented, **disabled** | Helped on 2 recordings, failed on 1 |
| Relay | Store-and-forward of whole messages, hop-by-hop ARQ, TTL; "delivered" for a routed message = next hop ACKed (no end-to-end ACK) | Simple and explainable; honestly labelled in the UI |
| NS-2 | Scripts + AWK written; **not run** | NS-2 not installed (needs sudo) |
| Optional M9 (4-FSK, X25519, file transfer) | Not implemented | Core reliability first |

Feature classes used throughout:

- **CORE** – the project does not work without it.
- **SYLLABUS** – exists to demonstrate a Computer Networks concept.
- **OPTIONAL** – only if time allows and the core is stable.

---

## 1. Project objectives

1. Two Windows laptops exchange text messages **through sound only** (speaker → air → microphone).
2. The data path implements real networking mechanisms: framing, addressing, CRC, ACK, timeout, retransmission, Stop-and-Wait, Selective Repeat, CSMA, ALOHA, ping/RTT and (with a 3rd laptop) multi-hop relay.
3. Every mechanism is visible in a simple PySide6 desktop app, so the professor can see the concept without reading code.
4. The code stays at second-year level and follows the JIIT CN lab socket/threading style.
5. Security (AES-GCM) is a separate, switchable module that never weakens the acoustic link. NS-2 is an OPTIONAL separate module, pending confirmation that the coursework needs it.

## 2. Functional requirements

| ID | Requirement | Class |
|----|-------------|-------|
| F1 | Select microphone and speaker; set volume; run calibration (noise floor + tone test) | CORE |
| F2 | Modulate bits into BFSK audio and play through the speaker | CORE |
| F3 | Capture microphone audio continuously; detect frame start; demodulate bits | CORE |
| F4 | Build/parse frames with sync byte, type, flags, source, destination, sequence, length, payload, CRC-16 | CORE |
| F5 | Fragment a message into frames and reassemble in sequence order | CORE |
| F6 | ACK frames, retransmission timer, Stop-and-Wait ARQ, duplicate detection | CORE |
| F7 | Engine runs a multithreaded TCP server on `127.0.0.1:5000`; the UI is a TCP client of it | CORE |
| F8 | Messages screen: choose destination node, type message, send, see delivery status and received messages | CORE |
| F9 | Error injection: "corrupt next frame", "drop next ACK", "drop frame N", "drop next SACK" | SYLLABUS |
| F10 | Packet monitor: every TX/RX frame with fields, CRC result, ACK/retransmission status | SYLLABUS |
| F11 | Performance: frames sent/received, CRC failures, retransmissions, timeouts, delivery ratio, goodput, RTT min/avg/max, loss % — all from real engine counters | SYLLABUS |
| F12 | Acoustic ping: Echo Request / Echo Reply frames, RTT and packet-loss summary like the ICMP Pinger lab | SYLLABUS |
| F13 | Parity vs CRC comparison on injected errors | SYLLABUS |
| F14 | Selective Repeat ARQ (window 4) with selective ACK | SYLLABUS |
| F15 | CSMA: sense channel energy before sending, defer with random backoff when busy | SYLLABUS |
| F16 | Pure ALOHA mode on the real link; Pure/Slotted ALOHA many-node **simulation**, plotted against theory (S = G·e⁻²ᴳ, S = G·e⁻ᴳ), clearly labelled SIMULATION | SYLLABUS |
| F17 | Device discovery: HELLO beacon over sound so "connected devices" is real, not assumed | SYLLABUS |
| F18 | Multi-hop relay A → B → C with a static routing table and TTL | SYLLABUS (needs 3rd laptop for real demo) |
| F19 | AES-GCM authenticated encryption of message payloads, pre-shared key | SYLLABUS (PBL security) |
| F20 | Simulated channel mode (software "air" with adjustable noise/loss), labelled SIMULATED everywhere | SYLLABUS / testing backup |
| F21 | NS-2 module: real `.tcl` scripts, run in WSL, AWK parsing of real trace files. Separate from the engine; never delays CORE work | **OPTIONAL / PENDING ACADEMIC REQUIREMENT** |
| F22 | Live monitor client: a small lab-style script that connects to the engine and prints events (proves the server handles several clients concurrently) | SYLLABUS |
| F23 | 4-FSK faster mode | OPTIONAL (only after BFSK is stable) |
| F24 | Diffie-Hellman (X25519) key exchange over the acoustic link | OPTIONAL |
| F25 | Small text-file transfer over sound | OPTIONAL |

**Out of scope:** native IP/ICMP over sound, sockets carrying the acoustic data, ultrasonic tones, DNS/HTTP/SMTP features, custom cryptography.

## 3. Non-functional requirements

- **Honesty:** no fake statuses, statistics, devices or simulated success. Simulated data is always labelled.
- **Offline:** no internet or external APIs at runtime.
- **Responsiveness:** audio and protocols run in background threads; the UI never freezes.
- **Thread safety:** shared state is reached only through `queue.Queue` or `threading.Lock`.
- **Python:** **Python 3.13 is the primary, verified target** (`.venv`). The code avoids 3.12+/3.13-only features so that 3.11–3.12 *should* work, but those versions are reported as **untested** until a test run is recorded. Python 3.14 is tested separately and reported as such.
- **Readability:** plain functions and small classes; every module and important function has a comment explaining *what network concept it implements*; no frameworks beyond PySide6.
- **Realistic speed:** the target is about **50 bit/s** (20 ms per bit); calibration may allow 100 bit/s. A 20-character message takes a few seconds, which is expected and documented.

## 4. System architecture

Each laptop runs one **SHOUT LAN node** made of two processes:

```
 ┌─────────────────────────────┐   TCP 127.0.0.1:5000    ┌──────────────────────────────────────────┐
 │  UI process (PySide6)       │ ◄─────────────────────► │  ENGINE process                          │
 │  = TCP CLIENT               │  commands / events      │  ControlServer (TCP SERVER, thread/client)│
 │  5 screens                  │  (local only, labelled  │  Application  : messages, ping, crypto   │
 └─────────────────────────────┘   "Local control link") │  Network      : routing table, relay     │
                                                         │  Data Link    : frames, CRC, ARQ         │
   optional monitor_client.py ──► same TCP server        │  MAC          : CSMA / ALOHA             │
                                                         │  Physical     : BFSK modem, audio I/O    │
                                                         └───────────────┬──────────────────────────┘
                                                                         │  SOUND ONLY ("Acoustic link")
                                                                         ▼
                                                              speaker ))) air ))) microphone
                                                                  of the other laptop(s)
```

**Two paths, always labelled in the UI:**

1. **Local control link (TCP)** – UI ↔ engine on the same laptop. It never leaves the machine and never carries laptop-to-laptop messages.
2. **Acoustic link (sound)** – engine ↔ engine between laptops. This is the network.

### Proposed folder structure

```
Shout_LAN/
├── run_engine.py            start an engine (node id, devices, channel mode)
├── run_ui.py                start the desktop app
├── monitor_client.py        lab-style TCP client that prints live engine events
├── requirements.txt
├── shout_lan/
│   ├── config.py            all shared constants (frequencies, bit time, port...)
│   ├── crc.py               CRC-16-CCITT and parity
│   ├── frame.py             frame build/parse (data-link + network header)
│   ├── modem.py             BFSK modulate / preamble detect / demodulate
│   ├── audio_io.py          sounddevice input stream + output playback
│   ├── channel_sim.py       SIMULATED channel (noise, loss) – labelled
│   ├── mac.py               CSMA and ALOHA access rules
│   ├── arq.py               Stop-and-Wait and Selective Repeat
│   ├── router.py            node table, routing table, relay/forwarding
│   ├── security.py          AES-GCM encrypt / decrypt, key file
│   ├── stats.py             counters, RTT, delivery ratio, throughput
│   ├── aloha_sim.py         many-node ALOHA simulation (labelled)
│   ├── engine.py            wires the layers + threads together
│   └── control_server.py    multithreaded TCP server (lab style)
├── ui/                      PySide6 screens + TCP client thread
├── tests/                   pytest unit + integration tests
├── ns2/                     .tcl scripts, awk/python analysis, results
├── tools/                   calibrate.py, gen_key.py, loopback_test.py
├── docs/                    the eight documents
└── course_material/         original course files (unchanged)
```

## 5. Client (UI) architecture

- `ui/control_client.py` – a TCP client (`socket.socket(AF_INET, SOCK_STREAM)`, `connect`, `sendall`, `recv`), same pattern as the lab client. A background `QThread` reads events and emits Qt signals, so the UI thread never blocks on `recv()`.
- Messages are one JSON object per line (newline-delimited JSON), e.g. `{"cmd": "send", "dst": 2, "text": "HELLO"}` and `{"event": "frame_tx", "seq": 3, ...}`.
- **Message boundaries:** TCP is a byte stream. One `recv()` may return half a message or several messages. The reader therefore keeps a receive buffer: it appends every `recv()` result, splits on `b"\n"`, decodes each complete line, and keeps the unfinished tail for the next `recv()`. `recv()` returning `b""` means the server closed the connection, and the UI shows "Disconnected". The same helper (`read_lines()`) is used by the server for commands, by `monitor_client.py`, and by the UI.
- **Sending:** commands are sent with `sendall()` (never `send()`), so the whole line is written even if the OS accepts it in parts. Only the UI thread sends commands, so the client needs no write lock.
- The **Connect & Audio** screen can start a local engine (subprocess) and then connect to it, showing real `connect()` errors such as *Connection refused*.

## 6. Server (engine) architecture

- `control_server.py` follows the lab pattern: `bind(("127.0.0.1", 5000))`, `listen(5)`, then a loop with `conn, addr = server.accept()` → `threading.Thread(target=handle_client, args=(conn, addr))`. Every connected client receives the event stream, so the UI and `monitor_client.py` can be connected at the same time.
- **Safe event delivery (one writer per socket):** each client gets its own `queue.Queue` of outgoing lines and **two threads**:
  - a *reader thread* that receives and handles commands;
  - a *writer thread* that is the **only** code allowed to call `sendall()` on that client's socket. It takes one complete JSON line at a time from the client's queue and sends it.

  Any engine thread that wants to publish an event calls `broadcast(event)`. This locks the client list, puts the encoded line into every client's queue, and unlocks. Command replies go through the same queue. Two JSON messages therefore can never interleave on one socket, and a slow client cannot block the engine.
- **Disconnects:** if `recv()` returns `b""` or `sendall()` raises `OSError`, that client is removed from the list (under the lock) and its threads end. Other clients are unaffected.
- Binding to `127.0.0.1` keeps the control link local by design (also part of the threat model).

## 7. Acoustic communication workflow

```
SEND:  text ─► [AES-GCM] ─► fragment ─► frame + CRC ─► MAC check ─► BFSK samples ─► speaker
RECV:  mic ─► chunks ─► preamble detect ─► bit decisions ─► frame parse ─► CRC check
          ─► ACK back (sound) ─► reorder/reassemble ─► [AES-GCM verify+decrypt] ─► display
```

The node is **half-duplex**: while it transmits, it ignores its own microphone input (its own frames are also filtered by source address).

## 8. Frame structure

Everything after the preamble is sent most significant bit first.

| Field | Size | Purpose / concept |
|-------|------|-------------------|
| Preamble | 100 ms chirp (1→4 kHz) | Frame detection + timing synchronization (Physical) |
| SYNC | 1 B `0x7E` | Frame boundary marker (Framing) |
| TYPE+FLAGS | 1 B | Type: DATA, ACK, SACK, PING, PONG, HELLO. Flags: LAST fragment, ENCRYPTED, POLL (end of SR burst) |
| LINK SRC | 1 B | Sender of *this hop* (MAC address equivalent, node 1–254) |
| LINK DST | 1 B | Receiver of *this hop*; 255 = broadcast |
| SEQ | 1 B | Sequence number 0–255 (ARQ, ordering, duplicates) |
| LEN | 1 B | Payload length, 0–24 bytes |
| NET header | 3 B | ORIGIN, FINAL DST, TTL (Network layer, used by the relay) |
| PAYLOAD | 0–24 B | Data (or ciphertext fragment) |
| CRC-16 | 2 B | CRC-16-CCITT over TYPE…PAYLOAD (Error detection) |

**Air-time arithmetic (used consistently in all documents):**

| Item | Bytes | Bits | Time at 50 bit/s |
|------|-------|------|------------------|
| Overhead: SYNC 1 + TYPE/FLAGS 1 + LINK SRC 1 + LINK DST 1 + SEQ 1 + LEN 1 + NET 3 + CRC 2 | **11** | 88 | 1.76 s |
| Full DATA frame (11 + 24 payload) | **35** | 280 | 5.6 s + 0.1 s preamble = **5.7 s** |
| ACK frame (no payload) | 11 | 88 | 1.76 s + 0.1 s = **1.86 s** |
| SACK frame (1-byte bitmap) | 12 | 96 | 1.92 s + 0.1 s = **2.02 s** |
| One Stop-and-Wait round, full frame | – | – | 5.7 + 1.86 + turnaround ≈ **8 s** |
- **Encapsulation is visible:** application data sits inside the network header, which sits inside the data-link frame, which travels as a BFSK signal. The packet monitor shows each layer.

## 9. Modulation and demodulation design

- **Sample rate** 48 kHz (both WASAPI devices default to 48 kHz).
- **BFSK:** bit 0 = f₀ and bit 1 = f₁. The defaults are 2000 Hz and 3000 Hz; the calibration tool measures the speaker → mic response and suggests the best pair. Each bit lasts 20 ms (adjustable to 10–40 ms), with short fades at its edges to avoid clicks.
- **Mapping:** bits → NRZ levels → tones. The UI shows the NRZ waveform next to the tone sequence (NRZ encoding is in the syllabus).
- **Detection:** cross-correlate the incoming audio with the known chirp. The correlation peak marks exactly where the frame starts.
- **Bit decision:** for each 20 ms slot, measure energy at f₀ and f₁ with the Goertzel algorithm (a cheap single-frequency DFT) and pick the larger. This is simple to explain and robust.
- **Volume and threshold** controls are in the UI. Calibration records the noise floor, which is also used as the CSMA busy threshold.

## 10. Reliability mechanisms

| Mechanism | Design |
|-----------|--------|
| CRC-16-CCITT | Polynomial 0x1021, init 0xFFFF; verified against the standard check value (`"123456789"` → `0x29B1`) |
| Parity | Even parity per frame, shown next to CRC in the error-injection demo (parity misses even-count bit errors) |
| ACK | Receiver sends ACK(seq) for every valid DATA frame, including duplicates (in case the earlier ACK was lost) |
| Timeout | Timeout = frame airtime + ACK airtime + margin, later adapted from measured RTT (like TCP's timer) |
| Stop-and-Wait | One frame in flight; up to 5 retries, then "delivery failed" is shown honestly |
| Selective Repeat | Window 4 with a selective-ACK (SACK) bitmap; full specification in §10.1 |
| Duplicates | (source, sequence) tracking; a duplicate is re-ACKed but delivered only once |

### 10.1 Selective Repeat specification

**Why a SACK bitmap:** the channel is half-duplex and an ACK costs about 1.86 s. Four separate ACKs would cost about 7.4 s; one SACK (about 2.0 s) carries the same per-frame information. Each frame is still acknowledged and retransmitted individually, which is what makes it Selective Repeat (unlike Go-Back-N).

**Parameters:**
- window size W = 4;
- sequence space 0–255 (≥ 2W, so an old duplicate can never be confused with a new frame);
- maximum 5 timeouts per message before reporting "delivery failed".

**Sender state:**
- `send_base`: the oldest unacknowledged sequence number;
- `next_seq`;
- for each sequence number in the window: the stored frame and an `acked` flag.

**Receiver state:**
- `rcv_base`: the next sequence number to deliver;
- a buffer holding at most W out-of-order frames.

**Normal operation:**
1. The sender transmits every unacked frame in the window (up to W) back-to-back. The **POLL** flag is set on the last one, and one retransmission timer starts.
2. The receiver handles each DATA frame that passes the CRC check:
   - **In window** `[rcv_base, rcv_base+W-1]` and not yet buffered: store it in the buffer.
   - **Already buffered, or just below `rcv_base`** (already delivered): it is a **duplicate**. Discard the payload, but still count it as received so it is reported in the SACK.
   - **Anything else:** ignore it.
3. **In-order delivery:** while the buffer contains `rcv_base`, pass that frame up to reassembly and advance `rcv_base`. A frame is passed up only at the moment `rcv_base` moves past it, so **no payload can be delivered twice**. Out-of-order frames simply wait in the buffer.
4. When the receiver gets a frame with POLL, it replies with a **SACK**:
   - the SEQ field holds `rcv_base` (everything before it has been received);
   - the 1-byte payload is a bitmap where bit *i* = 1 means frame `rcv_base + i` is in the buffer.
5. **Identifying missing frames:** the sender marks every sequence number below SACK.SEQ as acked, plus every sequence number whose bitmap bit is 1. Any frame in the window still unmarked is **missing**. The sender slides `send_base` forward and goes back to step 1, which sends **only the missing frames** plus any new frames that now fit in the window.

**Loss cases:**

| What is lost | What happens |
|--------------|--------------|
| A DATA frame (not the POLL one) | Its bitmap bit is 0 in the SACK, so only that frame is resent |
| The POLL frame itself | The receiver sends no SACK. The sender's timer expires, it resends the **oldest unacked frame with POLL set**, and that triggers a SACK |
| **The SACK** | The sender's timer expires. It resends the oldest unacked frame with POLL set. If the receiver already has that frame it is a duplicate (discarded, not delivered again), but the receiver still answers with a **fresh SACK showing its current state**. The sender then learns everything the lost SACK would have told it. Resending one frame instead of the whole window keeps the recovery cost to one frame plus one SACK |
| A corrupted SACK | CRC failure, treated exactly like a lost SACK |
| Repeated losses | After 5 timeouts the message is reported as "delivery failed"; there is never a false "delivered" |

**Error injection for the demo:** "drop frame N" (shows that only frame N is resent) and "drop next SACK" (shows recovery without duplicate delivery).

## 11. Threading architecture

| Thread | Job | Talks through |
|--------|-----|---------------|
| Audio input callback (sounddevice) | Copies mic chunks | `rx_audio_queue` |
| Receiver / demodulator | Finds preambles, decodes frames, checks CRC | `rx_frame_queue` |
| Link / protocol | ARQ state, ACKs, timers, reassembly, routing | `tx_queue`, events |
| Transmitter | Runs the MAC check, modulates, plays audio, sets "transmitting" flag | `tx_queue` |
| Control server accept loop | `accept()` new UI/monitor clients | – |
| Reader thread per client | Reads commands with a line buffer | engine command calls |
| Writer thread per client | The **only** thread that calls `sendall()` on that socket | that client's own outgoing `queue.Queue`, filled by `broadcast()` under the client-list lock |
| UI main thread + reader QThread | Qt widgets + `recv()` loop | Qt signals |

This is the **producer–consumer** pattern from the syllabus; shared statistics are protected with a `threading.Lock`.

## 12. User-interface structure (5 screens)

A sidebar with 5 items, a light theme with one accent colour, and the status always visible in the top bar ("Engine: connected / Node 1 / Acoustic link / Mode: Stop-and-Wait").

1. **Overview** – engine status, own node ID, devices discovered by HELLO (with last-heard time), a simple topology drawing, recent activity.
2. **Connect & Audio** – start engine / connect (host, port), mic and speaker selection, volume, calibrate, test tone, channel mode (Acoustic / SIMULATED).
3. **Messages** – destination, text box, send, chat history with per-message status (sending → frame 2/3 → delivered / failed), encryption toggle, ping button.
4. **Packet Monitor & Logs** – table of frames (direction, type, src, dst, seq, len, CRC ✓/✗, ACK, retries), click a row to see its layers and bits, plain-English event log, error-injection buttons.
5. **Protocols & Performance** – protocol selector (Stop-and-Wait / Selective Repeat, CSMA / ALOHA) with a 2-line plain-language explanation of each; live metrics labelled "Measured – acoustic link" or "Measured – SIMULATED channel"; ALOHA simulation chart labelled "Simulation".

## 13. Syllabus-to-feature mapping (summary)

| Syllabus topic | Feature | Real or simulated |
|----------------|---------|-------------------|
| Physical layer, transmission media, noise, attenuation | Speaker–mic link, calibration, SNR readout | Real |
| Digital encoding, NRZ | BFSK + NRZ view | Real |
| Framing | Frame format, sync, length | Real |
| Error detection, parity, CRC | CRC-16 + parity demo | Real |
| Error control, Stop-and-Wait | ARQ | Real |
| Sliding window | Selective Repeat | Real |
| ALOHA | Real Pure ALOHA mode + many-node simulation | Both, labelled |
| CSMA | Channel sensing | Real |
| Network layer, routing, forwarding | Relay with routing table, TTL | Real (3rd laptop) / SIMULATED channel |
| ICMP, ping, RTT, packet loss | Acoustic ping | Real (ICMP-*like*, not native ICMP) |
| Timers and retransmission | Adaptive timeout | Real |
| Sockets, TCP, multithreaded server | Control server + UI client + monitor client | Real |
| Security (AES, key management) | AES-GCM + threat model | Real |
| NS-2 topology, routing, performance | `ns2/` module | Real NS-2 simulation — OPTIONAL / PENDING academic confirmation |
| OSI / TCP/IP layering, encapsulation | Layer view in the packet monitor | Real |

The full table with "how to demonstrate" is planned for `docs/SYLLABUS_MAPPING.md`.

## 14. Security design (summary; details in THREAT_MODEL.md)

- **Algorithm:** AES-256-GCM from the `cryptography` library (an authenticated encryption standard; no custom crypto).
- **Key:** 32-byte pre-shared key in `secret.key`, made by `tools/gen_key.py` and copied to both laptops (same idea as Assignment 9). It is never transmitted and never committed to git.
- **Applied per message, not per frame:** `nonce (12 B) ‖ ciphertext ‖ tag (16 B)` is created once and then fragmented.
  - The overhead is **28 bytes per message** = 224 bits = **4.48 s** extra at 50 bit/s.
  - If those 28 bytes push the message over a 24-byte frame boundary, they also add one or two extra frames (11 bytes overhead + preamble, 1.86 s each) and their ACK rounds.
  - Both effects will be measured and reported.
- **Nonce rules (critical for GCM):**
  - Every encrypted message gets a **new 96-bit nonce from `os.urandom(12)`** (the operating system's cryptographically secure random generator). The nonce is sent in clear in front of the ciphertext; this is normal and safe.
  - The same (key, nonce) pair must **never** be used twice: reuse would reveal the XOR of two plaintexts and allow tag forgery.
  - The nonce is **never** derived from the frame sequence number or a counter that restarts. SEQ wraps at 256 and restarts with the program, so it would repeat.
  - With random 96-bit nonces, NIST SP 800-38D allows up to 2³² messages per key. SHOUT LAN will send at most a few thousand per key, so the chance of a repeat is negligible. A new key is generated for each demo/semester (`tools/gen_key.py`).
  - **Replay protection:** the receiver remembers the nonces of the last 1000 accepted messages and rejects a repeat as a replay.
- **Associated data:** origin and final destination are authenticated, so a relay or attacker cannot change them unnoticed. Link-hop fields (LINK SRC/DST, SEQ) are not covered, because a relay must rewrite them.
- **Limitation of one pre-shared key:**
  - All nodes share the same key. A valid tag proves only that the message came from *some device holding the key*, not *which node*.
  - Any keyholder can put another node's ID in ORIGIN and impersonate it, and can decrypt all traffic.
  - Telling nodes apart would need per-pair keys or digital signatures (e.g. Ed25519). This is out of scope and is documented as a known limitation in THREAT_MODEL.md.
- **Optional:** encryption can be switched off, so the basic link can be tested on its own.

## 15. Testing strategy

| Level | What | How |
|-------|------|-----|
| Unit | CRC, parity, frame build/parse, modem round trip (with added noise), ARQ state machines, SR window, routing, AES-GCM incl. tamper rejection | `pytest`, no audio hardware needed |
| Integration (simulated) | 2–3 engines exchanging messages over the SIMULATED channel with controlled loss | `pytest` |
| Hardware, single laptop | This laptop's speaker → its own mic: BER and frame success at several volumes and bit times | `tools/loopback_test.py`, results saved |
| Hardware, two laptops | Scripted checklist you run: 10 messages, error injection, ping, CSMA defer | `docs/TESTING.md` checklist, you report results |
| UI | Every button exercised against a running engine; no unhandled exceptions | Manual checklist + log review |
| Control link | Line-buffer reassembly fed with split and merged byte chunks; 2 clients + concurrent broadcasts check that no JSON lines are interleaved | `pytest` |
| Python versions | Full suite on 3.13 (primary). Separate run on 3.14. 3.11/3.12 reported as "untested" unless a run is recorded | Testing report |

## 16. Development phases (in your required priority order)

| # | Milestone | Main output |
|---|-----------|-------------|
| M0 | Environment setup (**not** application progress) | Git repo, `course_material/`, venv, requirements; evidence in TESTING.md |
| M1 | **Acoustic link** | Tone generation, calibration, preamble detection, BFSK bits recovered through real speaker → mic |
| M2 | Framing + sync + addressing + CRC-16 | Short text message sent and received as frames; corruption detected |
| M3 | ACK + timeout + Stop-and-Wait | Reliable delivery despite dropped frame / dropped ACK |
| M4 | Engine threads + TCP control server + UI + monitor + stats + ping | Working app on the core link |
| M5 | Security | AES-GCM, measured overhead, threat model |
| M6 | Selective Repeat, CSMA, ALOHA (+ simulation) | Protocol screen working |
| M7 | Multi-hop relay | Tested on the SIMULATED channel; real only with a 3rd laptop |
| M8 | NS-2 module — **OPTIONAL / PENDING** | Only if you confirm it's required. Real scripts, traces, metrics; never blocks M1–M7 |
| M9 | 4-FSK | OPTIONAL |
| M10 | Docs + final verification | All required documents, two-laptop test with you, final DoD checklist |

**Gate rule:** M2 does not start until M1 recovers bits reliably on real hardware; M6 does not start until M3 delivers messages reliably.

**Evidence rule:** a milestone is ticked only when its DoD criteria have test evidence recorded in `docs/TESTING.md`. Creating files or folders is never counted as completion.

## 17. Known limitations and technical risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| The Intel "Smart Sound" mic array applies noise suppression / automatic gain, which may treat steady tones as noise | Tones fade or are distorted | Try the WASAPI and WDM-KS raw inputs, turn off Windows audio enhancements (documented), choose tones by calibration |
| Very low data rate (~50 bit/s) | Long messages are slow; ARQ waits take seconds | Short messages, 24-byte frames, SR mode, clear progress display |
| Room noise / echo | CRC failures | Preamble correlation, Goertzel decisions, ARQ retries, quiet demo spot, external speaker as a fallback |
| Clock drift between laptops' sound cards | Bit slips in long frames | Short frames (≤ 35 B), re-sync on every preamble |
| Developing on one laptop | Two-laptop behaviour (distance, different hardware) is untested until you test | Loopback testing here + a scripted two-laptop checklist for you |
| CSMA sensing is imperfect (hidden nodes, echo) | Collisions still possible | Documented honestly; ARQ recovers |
| NS-2 install needs your sudo password in WSL | Optional M8 can't run until installed | Pending your decision; never blocks CORE work |
| Only Python 3.13 and 3.14 are installed here | 3.11/3.12 can't be verified locally | Reported as untested; avoid newer-only syntax |
| AES-GCM adds 28 B per message | 4.48 s extra per message, possibly an extra frame | Per-message (not per-frame) encryption; can be switched off for basic tests |
| One shared key for all nodes | Keyholders can impersonate each other | Documented limitation; per-node signatures out of scope |
| Random-nonce reuse (theoretical) | Would break GCM confidentiality/integrity | `os.urandom(12)` per message, ≪ 2³² messages per key, new key per demo |
