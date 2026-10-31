# SHOUT LAN — Professor Demonstration Script (two laptops)

Total ≈ 15–20 minutes. Laptop A = node 1, laptop B = node 2. Both have the project, the same `secret.key`, and have passed `docs/HARDWARE_TEST_PLAN.md` §0.

## Before the professor arrives (10 min)
1. On **both** laptops, close every program that can play sound (browser tabs, Teams, music). Windows volume ~70 %, fixed.
2. Place the laptops 0.5–1 m apart, speakers facing each other, in a quiet spot.
3. On both: `.venv\Scripts\python run_ui.py`, then **Connect & Audio**:
   - Node ID 1 (A) / 2 (B);
   - **Acoustic link (sound)**; leave the devices as pre-selected (WASAPI); Exclusive mic mode ✓;
   - **Start engine**.
   - Check the chip says *"Engine running … on the acoustic link"* and the mic meter shows "speaker **exclusive**". If it says SHARED, close the other audio app, then Stop/Start the engine.
4. On A: **Calibrate**. Note the recommended tones (from `python tools/loopback_test.py --calibrate`, or the default 2500/3500). Set the **same** tones on both laptops with **Apply tones**.
5. Optional, also on A: `.venv\Scripts\python monitor_client.py` in a terminal (second TCP client).
6. Backup if the room is too noisy: on ONE laptop start node 1 and node 2 as SIMULATED engines (ports 5001/5002) and say clearly that it is the simulated channel.

## Live demonstration
| Step | Do | Professor sees | Concept |
|---|---|---|---|
| 1 | Point at the top bar | "Local control link (TCP): connected 127.0.0.1:5000" and "Acoustic link (sound)" | TCP is local only; laptops talk only by sound |
| 2 | A: **Overview → Discover** | A beeps; B answers; node 2 appears with "heard N s ago"; topology line | Broadcast discovery; only nodes actually heard are listed |
| 3 | A: **Messages** → to node 2, "HELLO WORLD" → Send | A beeps the frame; B shows the message; A shows "✓ Delivered (ACK received)" only after B's ACK beep | Framing, CRC, ACK |
| 4 | A: **Packet Monitor** → click the DATA row | Layers: application data, network header (origin/final/TTL), data-link header, CRC verified, airtime | Encapsulation, OSI layers |
| 5 | A: **Corrupt next DATA frame**, send "CRC TEST" | B logs `CRC mismatch` (red); A logs timeout → retransmission → delivered | Error detection + ARQ |
| 6 | B: **Drop next ACK**, A sends "ACK TEST" | A retransmits; B: "Duplicate … not delivered twice"; the message appears **once** | Lost ACK, duplicates, exactly-once |
| 7 | A: **Protocols** → Selective Repeat; Monitor: frame 2 → **Lose this frame**; send a ~90-character message | A sends 4 frames, frame 2 "NOT sent (injected)"; B "buffered out of order"; one SACK; **only frame 2** resent | Sliding window, Selective Repeat, SACK |
| 8 | **Protocols** → CSMA; send from A, and from B while A is still beeping | B logs "CSMA: channel BUSY … deferring" | Carrier sense |
| 9 | **Protocols** → Pure ALOHA; **Run simulation** | Chart: simulated points on the theory curves (max 18.4 % / 36.8 %), labelled *Simulation* | ALOHA throughput |
| 10 | A: Messages → tick **Encrypt (AES-GCM)**, send "SECRET" | 🔒 on both; monitor shows ciphertext (ENC) and the extra frame | AES-GCM, security cost |
| 11 | A: **Ping this node ×4** | Replies with RTT; summary min/avg/max, loss % | ICMP-like ping, RTT |
| 12 | **Performance** page | Measured counters labelled "Measured – acoustic", RTT chart, delivery ratio, goodput | Performance metrics |
| 13 | (3rd laptop, or SIMULATED) Overview → route "3 via 2", send to 3 | Node 3 shows "2 hops"; relay logs "Relaying … TTL 4 → 3" | Routing, forwarding, TTL |
| 14 | Show `monitor_client.py` printing the same events | Two TCP clients served at once | Multithreaded socket server |
| 15 | (If NS-2 is required) `ns2/` topology + AWK metrics | Trace-based throughput/delay/drops | NS-2 (separate simulation) |

## If something goes wrong
- **Frames keep failing CRC:** move the laptops closer, check the "speaker exclusive" note, re-run Calibrate and apply the same tones on both laptops.
- **"Speaker in SHARED mode":** another program is using audio. Close it, then Stop engine / Start engine.
- **No ACK ever arrives:** B's engine may not be running, or the tones differ. Check B's Connect page.
- **Fallback:** the SIMULATED channel demo (clearly labelled) shows every protocol feature without sound.
