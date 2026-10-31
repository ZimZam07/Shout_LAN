# SHOUT LAN — Syllabus Mapping

Evidence column:
- **A** = verified over real sound (single-laptop loopback);
- **S** = verified in automated silent / SIMULATED tests;
- **P** = pending a hardware test (`docs/HARDWARE_TEST_PLAN.md`).

| Syllabus topic | Implemented in | Where in the app | How to demonstrate | Evidence |
|---|---|---|---|---|
| OSI / TCP/IP layering, encapsulation | `frame.py` (data link + network headers), `engine.py` | Packet Monitor → click a frame → "Frame layers" | Show application data inside the network header inside the data-link frame inside the sound | S, A |
| Physical layer, transmission medium | `audio_io.py`, `channel.py` | Top bar "Acoustic link (sound)"; Connect & Audio | Unplug/mute the speaker: frames stop arriving | A |
| Digital encoding / modulation (BFSK, NRZ) | `modem.py` | Connect & Audio (tones), monitor (bits/airtime) | Two tones, 50 bit/s; frame airtime shown per frame | A |
| Attenuation, noise, SNR | `calibration.py`, equaliser in `modem.py` | Connect & Audio → Calibrate (SNR chart, noise floor, mic meter) | Calibrate; lower the volume and watch the SNR drop | A (method), P (demo room) |
| Synchronisation | chirp preamble + correlation (`modem.find_preamble`) | Monitor: "preamble score" per frame | Frames are found in continuous microphone audio | A |
| Framing | `frame.py` (SYNC, LEN, flags), `receiver.py` | Packet Monitor table | Show header fields; a 3-fragment message is reassembled | A, S |
| Error detection: parity vs CRC | `crc.py` | Monitor: CRC ✓ / ✗ | "Corrupt next DATA frame" → receiver shows `CRC mismatch`; parity misses 2-bit errors (unit test) | A, S |
| Error control, Stop-and-Wait ARQ | `arq.StopAndWaitSender/Receiver` | Protocols page; monitor; log | Corrupt a frame / drop an ACK → timeout, retransmit, delivered once | A, S |
| Timers & retransmission (TCP RTO idea) | `arq.RetransmissionTimer` | Protocols page "Retransmission timeout" | RTO adapts after a few messages | A, S |
| Sliding window: Selective Repeat | `arq.SelectiveRepeatSender/Receiver` | Protocols page (select it); monitor (DATA-SR, SACK) | "Lose this frame" → only that frame resent; out-of-order frames buffered | A, S |
| MAC: CSMA | `mac.py`, `engine._carrier_sense` | Protocols page (MAC = CSMA, "Channel busy/idle"), log | Node defers while another node transmits | A (loopback), S |
| MAC: ALOHA (pure / slotted) | `mac.aloha_backoff`, `aloha_sim.py` | Protocols page: MAC = Pure ALOHA; **Simulation** chart | Backoff events after missing ACKs; simulation matches G·e^(−2G) and G·e^(−G) | S (simulation is labelled) |
| Network layer: addressing, routing, forwarding, TTL | `engine.routes/_forward`, NET header | Overview → Routing table, topology | Route "3 via 2": node 3 receives "2 hops"; routing loop dropped by TTL | S; P (real 3 laptops) |
| ICMP-like ping, RTT, packet loss | `engine._ping_job` | Messages → "Ping this node ×4"; Performance tiles | min/avg/max RTT and loss % (not native ICMP) | A, S |
| Performance: throughput, delay, delivery ratio | `stats.py` | Protocols & Performance | Goodput, RTT chart, delivery ratio, frame loss, labelled by source | A, S |
| Sockets: TCP client-server (lab style) | `control_server.py`, `ui/control_client.py`, `monitor_client.py` | Connect & Audio → "Local control link (TCP)" | Start the engine, connect, show `monitor_client.py` as a second client | S |
| Multithreading, producer-consumer, synchronisation | engine threads, per-client reader/writer threads, locks/queues | (behaviour) UI never freezes during transmission | Stress test: 2 clients × 4000 events, no interleaving; UI stall ≤ ~30 ms | S |
| Security: symmetric encryption (AES), key management | `security.py` (AES-256-GCM), `tools/gen_key.py`, THREAT_MODEL | Messages → "Encrypt (AES-GCM)"; monitor shows ciphertext | Encrypted message delivered; wrong key → rejected; relay forwards ciphertext | A (loopback), S |
| UDP | `UdpSimulatedChannel` (local UDP "air" for the SIMULATED mode) | Connect & Audio → SIMULATED channel | Two engines on one laptop exchange frames over UDP (labelled SIMULATED) | S |
| NS-2 topology design and performance (Eval 2) | `ns2/shoutlan_topology.tcl`, `ns2/analysis.awk` | separate module | `ns shoutlan_topology.tcl` + `awk -f analysis.awk out.tr` | AWK script verified on a hand-written trace; **simulation not run** (NS-2 not installed) |
