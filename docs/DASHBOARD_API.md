# SHOUT LAN — Dashboard contract

The browser dashboard replaces the old PySide6 app. This file is the interface between the three parts being built in parallel: the **link** (modem, Noise Shield), the **server** (launcher, HTTP bridge) and the **web app**. Change it only by agreement; if you must deviate, write the deviation at the bottom under "Deviations".

## 1. How it runs

```
python shout.py --node 1                 # real sound, this laptop's speaker + mic
python shout.py --node 1 --pair          # ONE laptop, nodes 1 and 2 sharing its speaker + mic (real sound)
python shout.py --node 1 --simulated     # no sound: nodes 1 and 2 in one process on a software "air"
```

- One process hosts the engine(s) and an HTTP server, then opens the browser.
- Node N is served at `http://127.0.0.1:(8080 + N)`; `--http PORT` overrides it for single-node runs. `--pair` and `--simulated` serve node 1 on 8081 and node 2 on 8082.
- Python standard library only for the server (no new pip dependencies). The web app is committed pre-built in `web/dist/`, so a demo laptop needs Python and nothing else. Everything works offline: no CDN, no remote fonts.
- The old TCP control server (`127.0.0.1:5000`, JSON lines) keeps running beside it; `monitor_client.py` still works.

## 2. HTTP

| Method | Path | Meaning |
|---|---|---|
| GET | `/` and static files | `web/dist/` (unknown paths fall back to `index.html`) |
| GET | `/api/status` | one JSON object, section 3 |
| GET | `/api/events` | Server-Sent Events, section 4 |
| POST | `/api/cmd` | JSON body `{"cmd": ...}`, section 5. Reply `{"ok": true}` or `{"ok": false, "error": "..."}` (HTTP 200 for both; 400 only for unparseable JSON) |

During web development the Vite dev server proxies `/api` to `http://127.0.0.1:8081`.

## 3. Status object

`engine.status()` as it is today, plus:

| Field | Type | Meaning |
|---|---|---|
| `shield` | bool | Noise Shield setting for frames this node SENDS |
| `shield_info` | object | `{"code": "Hamming(7,4)", "rate": 0.571, "filter_hz": [lo, hi]}` (whatever was really built; shown in the UI) |
| `noise` | object | `{"on": bool, "level": 0..1}` — the interference generator (section 6) |
| `mode` | string | `"acoustic"`, `"pair"` (one laptop, real sound) or `"simulated"` |
| `peer` | int or null | default destination node (the other node in pair/simulated mode; else the last node heard) |
| `http_port` | int | this node's dashboard port |
| `other_dashboards` | list | `[{"node": 2, "url": "http://127.0.0.1:8082"}]` in pair/simulated mode, else `[]` |

Existing fields the UI relies on: `node_id`, `channel`, `channel_label`, `running`, `transmitting`, `protocol`, `nodes`, `stats` (`counters`, `delivery_ratio`, `frame_loss`, `goodput_bps`, `rtt`), `freq0`, `freq1`, `bit_time`, `volume`, `mic_level`, `speaker_mode`.

New counters in `stats.counters`: `bits_repaired`, `frames_repaired` (frames that passed the CRC only because the shield corrected bits), `shield_frames_rx`, `shield_frames_tx`.

## 4. Events (SSE)

Each SSE message is `data: <one JSON object>\n\n`. Every object has `event`, `time` (unix seconds, float), `node`, and usually `text` (a plain-English sentence, ready to show in a log). On connect the server first replays the last 300 events of this run with `"replay": true` added, then sends `{"event": "replay_done"}`, then goes live. A comment line (`: ping`) is sent every 10 s to keep the connection open.

### Existing engine events (unchanged)
`engine_started`, `engine_stopped`, `error`, `tx_start`, `frame_tx`, `frame_rx`, `message_status` (`status`: queued / sending / delivered / failed), `message_rx`, `duplicate`, `ack_dropped`, `injection_set`, `setting`, `channel_busy`, `channel_idle`, `backoff`, `ping_start`, `ping_reply`, `ping_timeout`, `ping_summary`, `calibration_start`, `calibration`, plus the routing / Selective Repeat / security events, which the new UI may ignore.

Sender-side `timeout` and `retransmit` events must exist (add them if the ARQ code only counts them today): `{"event": "timeout", "seq", "msg_id", "attempt", "waited"}` and `{"event": "retransmit", "seq", "msg_id", "attempt"}`.

### Added fields

`frame_tx` adds: `shield` (bool), `bits_on_air` (int, after coding), `airtime` already exists.

`frame_rx` adds:

| Field | Type | Meaning |
|---|---|---|
| `shield` | bool | this frame arrived in the shielded (coded) format |
| `bits_repaired` | int | bits the shield corrected in this frame (0 for plain frames) |
| `repaired_at` | list of int | bit positions (in the decoded frame) that were corrected, at most 64 |
| `crc_rx` / `crc_calc` | string | e.g. `"0x9570"` / `"0x9396"`; present when a full frame was decoded (ok or not) |
| `bit_conf` | list of float | per-bit decision confidence 0..1, one value per decoded frame bit, rounded to 2 decimals (lets the UI draw the frame's bits and show which were weak) |
| `snr_db` | float | tone-band signal vs noise estimate for this frame |
| `frame` | object | for `ok: false` frames too, when the header decoded: best-effort `describe()` of the damaged frame, with `"damaged": true` |

### New events

| Event | Fields | When |
|---|---|---|
| `spectrum` | `bins` (list of 48 floats, dB, clamped −90..0), `f_lo`, `f_hi` (Hz, the range the bins span linearly; 500..6000), `level` (mic RMS 0..1), `tone0`, `tone1` (dB at the two tone frequencies), `noise_db` (median of the bins outside the tones), `transmitting` (bool) | about 12 times a second while the engine runs. NOT kept in the replay buffer |
| `preamble` | `score`, `shield` (which format the chirp announced) | a frame start was detected (lets the UI show "hearing a frame…" before it is decoded) |
| `shield_set` | `value` (bool), `text` | the shield setting changed |
| `noise_set` | `on`, `level`, `kind`, `text` | the interference generator changed |
| `replay_done` | — | end of the replay on connect |

## 5. Commands

All existing commands (`send`, `ping`, `hello`, `inject`, `set_volume`, `set_tones`, `set_protocol`, `set_mac`, `calibrate`, `reset_stats`, `status`, `shutdown`) work over `POST /api/cmd` exactly as over TCP. New:

| Command | Meaning |
|---|---|
| `{"cmd": "set_shield", "value": true}` | Noise Shield for frames this node sends. Receivers decode both formats automatically, and a node answers (ACK, PONG) in the format of the frame it is answering, so ONE switch on the sending laptop controls the whole exchange |
| `{"cmd": "set_noise", "on": true, "level": 0.8, "kind": "clicks"}` | interference generator, section 6. `kind` is `"hiss"` or `"clicks"`; `level` and `kind` may be omitted (omitted kind = keep the current one, default `"clicks"`; omitted level = keep the current one, or the kind's default: clicks 0.8, hiss 0.25) |
| `{"cmd": "clear"}` | forget the replay buffer and reset statistics (a clean slate before the demo) |

## 6. What the two demo controls really do

**Noise Shield** (engine-side; both laptops run the same code):
- OFF — plain BFSK frame, as before. Room noise flips bits; the CRC rejects the frame; the sender times out and retransmits.
- ON — the frame bytes are protected by a forward-error-correcting code (Hamming(7,4) with interleaving unless measurements pick something better), announced by a different preamble chirp so the receiver knows which decoder to use, and the receiver band-pass filters the microphone around the tone band before detection. Damaged bits are repaired without a retransmission; `bits_repaired` reports how many.

**Interference generator** (`set_noise`): this laptop's speaker really plays band-limited noise (it is real sound in the room, not a number changed in software). It makes the demo repeatable: the same noise level corrupts plain frames and is survived by shielded frames. It is always labelled as generated noise in the UI and in event text. In simulated mode it adds noise to the software air and is labelled SIMULATED.

Two kinds (`noise.kind`):
- `"clicks"` (default, what the demo uses): an 18 ms burst on one of our two tone frequencies every 0.52 s (26 bit times). This is impulsive noise of exactly the kind error correction with interleaving handles well, and it is presented as such: each burst damages only a few ADJACENT bits, the interleaver spreads those over different codewords, and the burst interval is chosen so two bursts rarely meet in one codeword. Plain frames are hit several times and fail their CRC. Measured offline (TESTING.md, "Clicks"): plain 16-byte DATA frames got through 8 % of the time and plain ACKs 18 %; shielded ones 86 % and 88 % first time. The shield does NOT beat all noise: steady loud hiss (kind `"hiss"`) at high levels defeats it too.
- `"hiss"`: steady band-limited noise over the tone band (the original generator).

The shield setting is read at the moment each frame goes on air: switching it on while a message is stuck retrying as plain frames makes the very next retry go out shielded.

## 7. Honesty rules (kept from the original project)

- No invented numbers: every value shown comes from an engine event or the status object.
- Simulated mode is labelled SIMULATED wherever a measurement is shown.
- Generated interference is labelled as generated.

## Deviations

Server (shout.py / shout_lan/web_bridge.py) — additions and clarifications, nothing in sections 1-5 removed:

- **Node ids / ports.** `--pair` and `--simulated` run nodes N and N+1 (`--node 1`, the default, gives 1 and 2 as specified). `--http P` also works there: node N on P, node N+1 on P+1. `--http 0` / `--port 0` = any free port (used by the tests). The TCP control link of the second node is on `--port`+1 (5001 by default).
- **READY line.** `READY mode=<acoustic|pair|simulated> node1=http://127.0.0.1:8081 [node2=http://127.0.0.1:8082]`, preceded by one `  node N: dashboard <url>  control 127.0.0.1:<port>  (<channel label>)` line per node.
- **Status extras.** `shield_available` and `noise_available` (bool: whether this engine build has `set_shield` / `set_noise`), `sse_clients` (int). Until the Noise Shield is merged, `shield` is `false`, `shield_info` is `{}` and `noise` is `{"on": false, "level": 0.0}`.
- **SIMULATED labelling.** In `--simulated` mode every SSE event (spectrum included) carries `"simulated": true`, and its `text` is prefixed with `[SIMULATED] ` unless it already says SIMULATED.
- **`cleared` event.** After `{"cmd": "clear"}` the server pushes `{"event": "cleared", "text": ...}` to the connected streams (live only, not replayed), so every open tab can wipe its log. `replay_done` also carries `count` (events replayed).
- **Command replies.** `{"cmd": "status"}` over HTTP replies `{"ok": true, "status": {...}}`. A JSON body that is not an object gives HTTP 200 `{"ok": false, ...}`; an empty or unparseable body gives 400. `set_shield` / `set_noise` on an engine without them reply `{"ok": false, "error": "... not available in this build ..."}`. `shutdown` over HTTP stops the whole process (both nodes in pair/simulated mode). Unknown `/api/...` paths give 404 JSON.
- **Spectrum details.** dB is relative to a full-scale sine (amplitude 1.0 = 0 dB); each of the 48 bins is the peak of the FFT bins inside it (4096-point Hann FFT). No `spectrum` event is sent before the node has heard any audio. `spectrum` events go only to the browser, not over the TCP control link. In SIMULATED mode the software air delivers a whole frame at once, so the received audio is replayed into the display at real-time speed; the node's own transmissions are added to its own display (a real microphone hears its own speaker; the software air does not); when nothing is on the air the display shows the simulation's background-noise model (the same noise level the simulated channel adds to every frame).

Link side (modem / shield / generator), 2026-11-18:

1. **`bits_repaired` vs `repaired_at`.** `bits_repaired` counts every on-air bit the shield decoder changed, parity bits included. `repaired_at` lists only the corrected *data* bits, as positions in the decoded frame (0 = first bit of SYNC), at most 64. So `len(repaired_at)` can be smaller than `bits_repaired`. The counter `frames_repaired` counts shielded frames in which at least one *data* bit was corrected (parity-only corrections would have passed the CRC anyway).
2. **`bit_conf` for shielded frames** has one value per decoded data bit: the demodulator's confidence in that bit as it was received, before correction.
3. **`shield_info`** has extra keys besides `code`, `rate`, `filter_hz`: `decoder` (soft decision), `interleaving`, `preamble`, `filter_use`, `airtime_factor` (1.75). The decoder is soft-decision Hamming(7,4): the valid codeword closest to what was heard, weighting each bit by its confidence.
4. **The receive filter is used for BOTH formats, for frame detection only.** `filter_hz` = `[900, 4500]` is the band-pass applied to the microphone audio before searching for either chirp. It is not a shield-only feature: it is the fix for the plain link's weak preamble detection on the MacBook (most mic noise was below 300 Hz). Bits are decoded from the unfiltered audio (Goertzel is already a narrow filter; a band-pass in front of it measured no gain).
5. **`timeout` / `retransmit` `attempt`** = which transmission of the frame: `timeout` attempt 1 = the first transmission got no ACK; `retransmit` attempt 2 = the second transmission. The `retransmit` event already existed; its `attempt` value is now 1-based in this sense (it used to be 1 for the first retransmission). With Selective Repeat, `timeout` carries the oldest unacknowledged `seq` and `attempt` = timeouts in a row.
6. **`set_noise`**: `level` may be omitted (then the current level, or the kind's default: clicks 0.8, hiss 0.25). `kind` ("hiss"/"clicks", omitted = keep current; the initial kind is "clicks") was added after the first contract; `noise` is `{"on", "level", "kind"}` and `noise_set` carries `kind`. For clicks, level = burst peak amplitude (1.0 = full scale); for hiss, level 1.0 = generated noise with the same RMS as our tones at the default transmit volume 0.5. In `--pair` mode both nodes share one speaker, so the generator is shared: switching it on from either node makes `noise.on` true on both (only the commanding node emits `noise_set`). In SIMULATED mode hiss added to the software air is scaled ×5 (clicks are not scaled) (`channel.SIM_NOISE_SCALE`), because the software air has no echo or fading and the same level would hardly hurt; the event text says SIMULATED. The multi-process UDP simulated channel has no generator (`set_noise` returns an error there).
7. **Extra fields**: `tx_start` also has `shield`. `frame_rx` with `ok: false` also has `shield`, `bits_repaired`, `snr_db`, `bit_conf`, and `crc_rx`/`crc_calc` when the whole frame was decoded; `frame` (with `"damaged": true`) is present only when the length byte was usable. `preamble` events are not emitted for a node's own transmissions on the acoustic channel.
8. **`snr_db`** is the tone contrast of the frame: average power of the sent tone over the average power of the other tone in the same bit slots (noise plus room echo), in dB.
9. **`erased`** (int, extra field on shielded `frame_rx`): bits the decoder treated as erased because their slot was much louder than the rest of the frame (drowned by a burst). `bits_repaired` counts every bit whose value the decoder changed, erased or not. `shield_info` has extra keys `erasures` and `limits` (a plain statement of what the shield cannot do).
10. The `clear` command is not handled in `node.py` (the replay buffer lives in the server); the server should call `engine.reset_stats()`.
