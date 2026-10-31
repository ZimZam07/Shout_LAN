# SHOUT LAN — Threat Model

## 1. What we protect
- **Message contents:** the text users send.
- **Message integrity:** what the receiver displays must be exactly what the sender wrote, and must claim the right sender and recipient.
- **Freshness:** an old message must not be accepted again as new.

## 2. The setting
- Sound is a **broadcast medium**. Every device within hearing range receives every frame, just like radio or a shared Ethernet hub.
- An attacker therefore needs no physical access to the laptops: a phone's microphone and speaker are enough.
- The local control link (TCP) is bound to `127.0.0.1` only, so it cannot be reached from other machines.

## 3. Security mechanism
Encryption is **AES-256-GCM** (authenticated encryption, NIST SP 800-38D), from the standard `cryptography` library. No home-made cryptography is used.

| Item | Design |
|---|---|
| Key | 32 random bytes in `secret.key`, made by `tools/gen_key.py` (`AESGCM.generate_key`). Copied to each laptop by hand (USB), **never** sent over the acoustic link. Listed in `.gitignore`. |
| Nonce | 96 bits from `os.urandom(12)` (the OS's cryptographically secure random generator), **new for every message**. Sent in clear before the ciphertext (normal for GCM). Never derived from SEQ or any counter that restarts. |
| Associated data | `"SHOUT-LAN v1" ‖ origin ‖ final destination`. These are authenticated (covered by the tag) but not secret. |
| Message format | `nonce (12) ‖ ciphertext (n) ‖ tag (16)` → **+28 bytes per message**. It is created once and then fragmented into frames, each flagged `ENC`. |
| Verification | The receiver reassembles the frames, then `AESGCM.decrypt`. Any change makes decryption fail (`InvalidTag`). The message is **discarded and logged**; nothing unverified is shown. |
| Replay cache | The receiver remembers the nonces of the last **1000** accepted messages and rejects a repeat. |

## 4. Threats and how they are handled

| Threat | Example | Handled? | How / why not |
|---|---|---|---|
| **Eavesdropping** | Someone records the beeps and decodes them with their own modem | **Yes** (encrypted messages) | Without the key the payload is AES ciphertext. *Not hidden:* the headers (source, destination, sequence numbers, length, type), message timing and size. These are traffic metadata, needed in clear for routing and ARQ. |
| **Tampering** | Attacker plays a modified frame | **Yes** | Changing the payload: the CRC-16 may still pass if the attacker recomputes it (CRC is *not* security), but the GCM tag fails and the message is rejected. Changing origin or final destination: fails too, because they are associated data. Tested: modified nonce, ciphertext, tag, origin and destination are all rejected. |
| **Replay** | Attacker records an encrypted message and plays it again later | **Partly** | Repeats among the last 1000 accepted messages are rejected (tested). **Limitations:** (1) the cache is in memory, so restarting the receiver forgets it; (2) after 1000 newer messages a very old one could be accepted again (tested and documented). A real protocol would add timestamps or per-sender counters. |
| **Spoofing / impersonation (outsider)** | A device without the key pretends to be node 1 | **Yes** | It cannot produce a valid tag; the receiver rejects the message. |
| **Spoofing by an insider** | Laptop 3 (which has the key) sends a message claiming to be node 1 | **No** | **All nodes share one key.** A valid tag proves the message came from *some* keyholder, not *which* one. Any keyholder can forge the origin field and read every message. Telling nodes apart would need per-pair keys or digital signatures (e.g. Ed25519); out of scope. |
| **Unencrypted traffic** | Messages sent with "Encrypt" off; ACKs, PING, HELLO frames | **No** (by design) | These are protected only by CRC-16, which detects accidental noise, **not** deliberate changes. Encryption is optional so that the basic link can be tested on its own. |
| **Jamming / denial of service** | Playing loud noise or tones in the 1–4 kHz band (preamble chirp and both data tones) | **No** | Physical-layer jamming can't be prevented in software. ARQ retries, then honestly reports "delivery failed". CSMA (M6) defers while the channel is busy, so constant noise blocks sending, and the UI shows why. |
| **Forged ACKs** | Attacker plays "ACK seq k" so the sender thinks a frame arrived | **No** | Link-layer ACKs are not authenticated. "Delivered" means "the data link received an ACK". An attacker could make a sender believe an undelivered message arrived. Authenticated ACKs would need a MAC per ACK frame (+16 bytes ≈ 2.6 s each at 50 bit/s). |
| **Key theft** | Copying `secret.key` from a laptop | **No** | Anyone with the file has full access. Mitigations: keep it out of git (done), make a new key for each demo, delete it after use. |
| **Nonce reuse** | Two messages encrypted with the same key and nonce | **Prevented** | Random 96-bit nonces; NIST allows up to 2³² messages per key with random nonces, and we send at most thousands. Tested: 10 000 encryptions gave 10 000 distinct nonces, and there is exactly one nonce source in the code. |
| **Remote access to the engine** | Another computer connects to port 5000 and sends commands | **Yes** | The control server binds to `127.0.0.1`, so it is reachable only from the same laptop. |

## 5. Cost of security (measured, not estimated)
Single-laptop acoustic loopback, `results/arq_20261031_183229.json`: "SECRET HELLO" (12 bytes)

| | Bytes on air (payload) | Data frames | Delivery time |
|---|---|---|---|
| Plain | 12 | 1 | **6.8 s** |
| AES-256-GCM | 40 (12 + 28) | 2 | **14.5 s** |

The 28-byte nonce+tag alone is 224 bits = **4.48 s** of sound at 50 bit/s. Here it also pushed the message over the 24-byte frame limit. That added a second frame, with its 11-byte header, preamble and ACK round, so the total cost was **+7.7 s (2.1×)**. For longer messages the relative cost is smaller, because the 28 bytes are paid once per message, not per frame.

## 6. Summary
- **Protected:** confidentiality and integrity of encrypted message contents against anyone without the key; detection of recent replays.
- **Not protected:** traffic metadata, unencrypted traffic, ACK authenticity, insiders who hold the shared key, and jamming.
