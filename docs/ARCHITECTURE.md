# SHOUT LAN — Architecture

## 1. Big picture

Each laptop runs **one node**, which is two programs:

```
 ┌──────────────── Laptop A ─────────────────┐            ┌──────────────── Laptop B ─────────────────┐
 │  UI (PySide6)  ── TCP 127.0.0.1:5000 ──►  │            │  ENGINE  ◄── TCP 127.0.0.1:5000 ──  UI    │
 │  = TCP client     "Local control link"    │            │                                            │
 │                   ENGINE (run_engine.py)  │  SOUND     │                                            │
 │                   speaker ))))))))))))))))┼──────────► │ microphone                                 │
 │                   microphone ◄────────────┼─────────── │ speaker  (ACKs, replies)                   │
 └───────────────────────────────────────────┘ "Acoustic  └────────────────────────────────────────────┘
                                                   link"
```

- **Acoustic link (sound):** the only path between laptops.
- **Local control link (TCP):** UI ↔ engine on the **same** laptop only. It is bound to `127.0.0.1`, so it never leaves the machine.
- **SIMULATED channel:** a clearly labelled backup and testing mode. It replaces only "the air"; the modulated audio samples are passed in software (local UDP between engine processes, or an in-memory object in tests).

## 2. Layers and modules

| Layer | Module | What it does |
|---|---|---|
| Application | `engine.py` (`send_message`, `_deliver`, `ping`, `hello`), `security.py` | text messages, acoustic ping (ICMP-like), discovery, AES-256-GCM |
| Network | `engine.py` (`routes`, `_message_complete`, `_forward`), `frame.py` NET header | origin / final destination / TTL, routing table, store-and-forward relay |
| Data link | `frame.py`, `crc.py`, `arq.py`, `receiver.py` | framing (SYNC, header, LEN), CRC-16-CCITT, fragmentation/reassembly, Stop-and-Wait, Selective Repeat (window 4, SACK), duplicates, adaptive timeout |
| MAC sublayer | `mac.py`, `engine.py` (`_carrier_sense`, `before_retransmit`) | CSMA (carrier sense in the tone band), Pure ALOHA (binary exponential backoff), none |
| Physical | `modem.py`, `audio_io.py`, `channel.py`, `calibration.py` | BFSK 50 bit/s, chirp preamble, scrambler, equaliser, Goertzel / sliding-DFT detection, WASAPI exclusive audio, calibration |
| Simulation (separate) | `aloha_sim.py`, `ns2/` | many-node ALOHA vs theory; NS-2 wired topology (not yet run) |

## 3. A frame on the wire

```
[ chirp 100 ms ][ gap 10 ms ][ SYNC | TYPE|FLAGS | SRC | DST | SEQ | LEN | ORIGIN | FINAL | TTL | PAYLOAD 0-24 | CRC-16 ]
 physical                     <---------------- data link ----------------><---- network ----->  application    data link
```

- Overhead is 11 bytes. A full frame is 35 bytes = 280 bits = 5.6 s at 50 bit/s, plus the 0.11 s preamble and gap.
- All bytes after the preamble are XOR-scrambled (802.11 LFSR, x⁷+x⁴+1) so that long runs of one tone can't build up room echo.
- Frame types: DATA (Stop-and-Wait), DATA-SR (Selective Repeat), ACK, SACK, PING, PONG, HELLO.
- Flags: FIRST, LAST, ENC (AES-GCM payload), POLL (end of a Selective Repeat burst).

## 4. Audio pipeline
**Send:** text → [AES-GCM] → fragments → frame + CRC → scramble → bits → BFSK samples (continuous phase) → `Speaker.play` (persistent WASAPI **exclusive** stream).

**Receive:**
1. `Microphone` callback (WASAPI **exclusive**) → chunk queue.
2. `FrameReceiver.feed`:
   - normalised cross-correlation with the chirp → frame start;
   - per-tone gain estimated from the preamble (equaliser);
   - each bit decided in the 40–95 % part of its slot (skipping the previous bit's echo), with Goertzel-equivalent tone powers;
   - header → LEN → whole frame → descramble → CRC check.
3. The engine then handles the frame.

**Why exclusive mode, on both sides (measured):**
- Microphone: Windows noise suppression removed steady tones (shared 44 % BER vs exclusive 0 %).
- Speaker: Windows shared-mode playback dropped whole buffers mid-frame (18.6 % vs 0 %).
- If the speaker can't be opened exclusively, the engine falls back to shared mode and the UI shows a warning.

**Experimental, off by default:** symbol-timing recovery (`Modem.TRACKING_DEFAULT`). It helped on 2 recordings and failed on 1.

## 5. Threads (producer → queue → consumer)

| Thread | Work | Shares data through |
|---|---|---|
| audio callback (sounddevice) | copy mic chunks | `Microphone.chunks` queue |
| `rx` | decode frames, ACK/SACK/PONG replies, deliver, relay | `tx_queue`, `ack_queue`, `pong_queue`, `outbox` |
| `tx` | play frames (ACKs first: `PriorityQueue`) | `tx_queue` |
| `sender` | messages / pings / HELLO / relayed messages, ARQ protocol | `outbox`, waits on `ack_queue` |
| control `accept` | `accept()` new TCP clients | client list (lock) |
| per client: reader + writer | commands in / events out; **only the writer calls `sendall()`** | each client's own queue |
| UI main + reader thread | Qt widgets; blocking `recv()` in the background | Qt signals |

- Shared counters are in `Stats` (lock).
- Half-duplex: while a node is transmitting, and for 0.15 s afterwards, its acoustic receiver discards audio, so it never decodes its own sound.

## 6. Reliability details
- **Stop-and-Wait:** one frame in flight; ACK = same SEQ. Retransmission timeout = SRTT + 4·RTTVAR (as in TCP RFC 6298), using Karn's rule. Max 5 retries, then "FAILED". The random initial SEQ protects against restart duplicates.
- **Selective Repeat:** window 4, a burst with POLL on the last frame, and **one SACK** = (next expected SEQ, 8-bit bitmap of buffered frames). Out-of-order frames are buffered and delivered in order. On a timeout only the oldest missing frame is resent with POLL, which recovers lost POLL frames and lost SACKs. Duplicates are reported but never delivered twice.
- **Relay:** store-and-forward of whole messages. Every hop has its own link-layer ARQ. TTL is decremented per hop and the message is dropped at 0. Encrypted payloads pass through untouched. "Delivered" at the sender of a routed message means **the next hop acknowledged it** (shown as such in the UI); there is no end-to-end ACK.

## 7. Control protocol (newline-delimited JSON over TCP)
- **Commands:** `status`, `send`, `ping`, `hello`, `inject`, `set_volume`, `set_tones`, `set_protocol`, `set_mac`, `set_route`, `calibrate`, `reset_stats`, `shutdown` (see `shout_lan/node.py`).
- **Events:** `frame_tx`, `frame_rx`, `message_status`, `message_rx`, `timeout`, `retransmit`, `sack_rx`, `channel_busy`, `backoff`, `forwarding`, `ttl_expired`, `security_reject`, `calibration`, `status`, … Each event has a plain-English `text` shown in the logs.
- **Message boundaries:** TCP may split or merge messages; `LineBuffer` rebuilds whole lines (UTF-8 safe).
