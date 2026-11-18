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
| `noise_set` | `on`, `level`, `text` | the interference generator changed |
| `replay_done` | — | end of the replay on connect |

## 5. Commands

All existing commands (`send`, `ping`, `hello`, `inject`, `set_volume`, `set_tones`, `set_protocol`, `set_mac`, `calibrate`, `reset_stats`, `status`, `shutdown`) work over `POST /api/cmd` exactly as over TCP. New:

| Command | Meaning |
|---|---|
| `{"cmd": "set_shield", "value": true}` | Noise Shield for frames this node sends. Receivers decode both formats automatically, and a node answers (ACK, PONG) in the format of the frame it is answering, so ONE switch on the sending laptop controls the whole exchange |
| `{"cmd": "set_noise", "on": true, "level": 0.5}` | interference generator, section 6 |
| `{"cmd": "clear"}` | forget the replay buffer and reset statistics (a clean slate before the demo) |

## 6. What the two demo controls really do

**Noise Shield** (engine-side; both laptops run the same code):
- OFF — plain BFSK frame, as before. Room noise flips bits; the CRC rejects the frame; the sender times out and retransmits.
- ON — the frame bytes are protected by a forward-error-correcting code (Hamming(7,4) with interleaving unless measurements pick something better), announced by a different preamble chirp so the receiver knows which decoder to use, and the receiver band-pass filters the microphone around the tone band before detection. Damaged bits are repaired without a retransmission; `bits_repaired` reports how many.

**Interference generator** (`set_noise`): this laptop's speaker really plays band-limited noise (it is real sound in the room, not a number changed in software). It makes the demo repeatable: the same noise level corrupts plain frames and is survived by shielded frames. It is always labelled as generated noise in the UI and in event text. In simulated mode it adds noise to the software air and is labelled SIMULATED.

## 7. Honesty rules (kept from the original project)

- No invented numbers: every value shown comes from an engine event or the status object.
- Simulated mode is labelled SIMULATED wherever a measurement is shown.
- Generated interference is labelled as generated.

## Deviations

(none yet)
