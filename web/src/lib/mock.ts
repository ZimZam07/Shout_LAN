import type { CmdReply, Command, ConnState, EngineEvent, FrameInfo, Status, Transport } from "./types";

/* A stand-in engine for building and rehearsing without sound. It speaks the
 * contract (docs/DASHBOARD_API.md) with the shapes of shout_lan/engine.py and
 * frame.py: real frame bytes, a real CRC-16, airtime from the bit rate, the
 * stop-and-wait timeout and retransmission, and a spectrum 12 times a second.
 *
 * Every number it produces is MOCK DATA; the app marks the screen as such.
 *
 * The air model is deliberately repeatable, because the pitch is:
 *   quiet room                -> every frame arrives
 *   generated clicks, no shield -> isolated flipped bits: tries 1-3 fail, try 4 lands
 *   generated hiss, no shield   -> try 1's receipt is damaged, try 2 lands
 *   generated noise + shield    -> first try lands, a few bits repaired (clicks most)
 * With `autopilot` it plays that story in a loop until someone touches a control. */

const NODE = 1;
const PEER = 2;
const BIT_TIME = 0.02; // 50 bit/s
const PREAMBLE = 0.11; // chirp + gap
const FREQ0 = 2500;
const FREQ1 = 3500;
const F_LO = 500;
const F_HI = 6000;
const BINS = 48;
const MAX_PAYLOAD = 24;
const TYPES: Record<string, number> = { DATA: 1, ACK: 2, PING: 4, PONG: 5, HELLO: 6 };
const F_LAST = 1;
const F_FIRST = 8;
const SHIELD_INFO = { code: "Hamming(7,4)", rate: 0.571, filter_hz: [900, 4500] as [number, number], decoder: "soft decision", interleaving: true, airtime_factor: 1.75 };
type Kind = "clicks" | "hiss";
const IDLE_BEFORE_AUTOPILOT_MS = 30_000;

class Stopped extends Error {}

function crc16(bytes: Uint8Array): number {
  let crc = 0xffff;
  for (const b of bytes) {
    crc ^= b << 8;
    for (let i = 0; i < 8; i++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
  }
  return crc;
}
const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(" ");
const hex16 = (v: number) => "0x" + v.toString(16).toUpperCase().padStart(4, "0");
const rand = (a: number, b: number) => a + Math.random() * (b - a);
const r2 = (v: number) => Math.round(v * 100) / 100;

interface MockFrame {
  type: string;
  flags: number;
  src: number;
  dst: number;
  seq: number;
  payload: Uint8Array;
}

function build(f: MockFrame): Uint8Array {
  const body = new Uint8Array(8 + f.payload.length);
  body.set([(TYPES[f.type] << 4) | f.flags, f.src, f.dst, f.seq, f.payload.length, f.src, f.dst, 4]);
  body.set(f.payload, 8);
  const crc = crc16(body);
  const raw = new Uint8Array(body.length + 3);
  raw[0] = 0x7e;
  raw.set(body, 1);
  raw.set([crc >> 8, crc & 0xff], body.length + 1);
  return raw;
}

function describe(f: MockFrame, payload = f.payload): FrameInfo {
  const flags: string[] = [];
  if (f.flags & F_FIRST) flags.push("FIRST");
  if (f.flags & F_LAST) flags.push("LAST");
  return { type: f.type, flags, src: f.src, dst: f.dst, seq: f.seq, len: f.payload.length, origin: f.src, final: f.dst, ttl: 4, payload_hex: hex(payload) };
}

type Air = { kind: "tx" | "rx"; start: number; dur: number } | null;

export class MockTransport implements Transport {
  readonly mock = true;
  private emitFn: (e: EngineEvent) => void = () => {};
  private alive = false;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private ticker: ReturnType<typeof setInterval> | null = null;
  private chain: Promise<void> = Promise.resolve();
  private pending = 0;
  private lastTouch = 0;

  private shield = false;
  private noise: { on: boolean; level: number; kind: Kind } = { on: false, level: 0.25, kind: "clicks" };
  private volume = 0.5;
  private seq = 0;
  private peerSeq = 40;
  private msgId = 0;
  private air: Air = null;
  private heard: number | null = null;
  private counters = this.zero();

  constructor(private autopilot = true) {}

  private zero(): Record<string, number> {
    return {
      frames_tx: 0, data_frames_tx: 0, frames_rx_ok: 0, crc_failures: 0, retransmissions: 0, timeouts: 0,
      messages_sent: 0, messages_delivered: 0, messages_failed: 0, messages_received: 0, acks_tx: 0, acks_rx: 0,
      duplicates: 0, bits_repaired: 0, frames_repaired: 0, shield_frames_rx: 0, shield_frames_tx: 0,
    };
  }

  // ── Transport ─────────────────────────────────────────────────────────────
  start(onEvent: (e: EngineEvent) => void, onConn: (c: ConnState) => void) {
    this.emitFn = onEvent;
    this.alive = true;
    onConn("live");
    this.emit("replay_done");
    this.emit("engine_started", { channel: "mock", label: "mock air", text: `Node ${NODE} started on the mock air` });
    this.ticker = setInterval(() => this.spectrum(), 1000 / 12);
    if (this.autopilot) this.story().catch(() => {});
  }

  stop() {
    this.alive = false;
    if (this.ticker) clearInterval(this.ticker);
    this.timers.forEach(clearTimeout);
    this.timers.clear();
  }

  async status(): Promise<Status> {
    return {
      node_id: NODE, channel: "mock", channel_label: "mock air", running: this.alive, transmitting: this.air?.kind === "tx",
      protocol: "Stop-and-Wait", nodes: this.heard ? { [PEER]: { last_heard: this.heard, score: 0.84 } } : {},
      stats: { source: "mock", counters: { ...this.counters }, delivery_ratio: null, frame_loss: null, goodput_bps: null },
      freq0: FREQ0, freq1: FREQ1, bit_time: BIT_TIME, volume: this.volume, mic_level: 0.02, speaker_mode: "mock", rto: this.rto(),
      shield: this.shield, shield_info: SHIELD_INFO, noise: { ...this.noise }, mode: "simulated", peer: PEER, http_port: 8081,
      other_dashboards: [],
    };
  }

  async cmd(c: Command): Promise<CmdReply> {
    if (!this.alive) return { ok: false, error: "The engine did not answer" };
    this.lastTouch = Date.now();
    switch (c.cmd) {
      case "send": {
        const text = c.text.trim();
        if (!text) return { ok: false, error: "empty message" };
        this.sendMessage(text, c.dst);
        return { ok: true };
      }
      case "set_shield":
        this.setShield(c.value);
        return { ok: true };
      case "set_noise":
        this.setNoise(c.on, c.level ?? (this.noise.level || 0.25), c.kind);
        return { ok: true };
      case "set_volume":
        this.volume = Math.max(0, Math.min(1, c.value));
        this.emit("setting", { name: "volume", value: this.volume, text: `Transmit volume set to ${this.volume.toFixed(2)}` });
        return { ok: true };
      case "hello":
        this.job(async () => {
          await this.transmit({ type: "HELLO", flags: 0, src: NODE, dst: 255, seq: this.nextSeq(), payload: new Uint8Array() }, 0, null);
          await this.sleep(350);
          await this.receive({ type: "HELLO", flags: 0, src: PEER, dst: 255, seq: this.nextPeerSeq(), payload: new Uint8Array() }, "ok");
        });
        return { ok: true };
      case "ping":
        this.job(async () => {
          const count = Math.max(1, Math.min(4, c.count ?? 2));
          this.emit("ping_start", { dst: c.dst, count, text: `PING node ${c.dst}: ${count} echo requests over the acoustic link` });
          for (let n = 0; n < count; n++) {
            const t0 = performance.now();
            await this.transmit({ type: "PING", flags: 0, src: NODE, dst: c.dst, seq: this.nextSeq(), payload: new Uint8Array([7, n]) }, 0, null);
            await this.sleep(250);
            await this.receive({ type: "PONG", flags: 0, src: c.dst, dst: NODE, seq: this.nextPeerSeq(), payload: new Uint8Array([7, n]) }, "ok");
            const rtt = (performance.now() - t0) / 1000;
            this.emit("ping_reply", { dst: c.dst, n, rtt: Math.round(rtt * 1000) / 1000, text: `Reply from node ${c.dst}: seq=${n} time=${(rtt * 1000).toFixed(0)} ms` });
            await this.sleep(400);
          }
        });
        return { ok: true };
      case "clear":
        this.counters = this.zero();
        return { ok: true };
    }
  }

  // ── plumbing ──────────────────────────────────────────────────────────────
  private emit(event: string, fields: Record<string, unknown> = {}) {
    if (this.alive) this.emitFn({ event, time: Date.now() / 1000, node: NODE, ...fields });
  }

  private sleep(ms: number) {
    return new Promise<void>((resolve, reject) => {
      if (!this.alive) return reject(new Stopped());
      const t = setTimeout(() => {
        this.timers.delete(t);
        if (this.alive) resolve();
        else reject(new Stopped());
      }, ms);
      this.timers.add(t);
    });
  }

  /** One thing on the air at a time, like the real half-duplex link. */
  private job(run: () => Promise<void>): Promise<void> {
    this.pending++;
    this.chain = this.chain
      .then(run)
      .catch((e) => {
        if (!(e instanceof Stopped)) this.emit("error", { text: `Mock engine error: ${String(e)}` });
      })
      .finally(() => {
        this.pending--;
      });
    return this.chain;
  }

  private nextSeq() {
    this.seq = (this.seq + 1) % 256;
    return this.seq;
  }
  private nextPeerSeq() {
    this.peerSeq = (this.peerSeq + 1) % 256;
    return this.peerSeq;
  }
  private airtime(bytes: number, shield: boolean) {
    const bits = shield ? Math.ceil((bytes * 8 * 7) / 4) : bytes * 8;
    return { bits, seconds: PREAMBLE + bits * BIT_TIME };
  }
  /** Long enough for the receipt to come back, as the engine's adaptive timer settles. */
  private rto() {
    return r2(this.airtime(11, this.shield).seconds + 1.7);
  }
  private noisy() {
    return this.noise.on && this.noise.level >= 0.1;
  }

  private setShield(value: boolean) {
    this.shield = value;
    this.emit("shield_set", {
      value,
      text: value ? `Noise Shield ON: frames are sent with ${SHIELD_INFO.code} error correction` : "Noise Shield OFF: frames are sent plain",
    });
  }

  private setNoise(on: boolean, level: number, kind?: string) {
    this.noise = { on, level: Math.max(0, Math.min(1, level)), kind: kind === "hiss" || kind === "clicks" ? kind : this.noise.kind };
    this.emit("noise_set", {
      on, level: this.noise.level, kind: this.noise.kind,
      text: on ? `SIMULATED generated noise ON (${this.noise.kind}) at level ${this.noise.level.toFixed(2)}` : "SIMULATED generated noise OFF",
    });
  }

  // ── sending and receiving frames ──────────────────────────────────────────
  private async transmit(f: MockFrame, retry: number, msgId: number | null, shield = this.shield) {
    const raw = build(f);
    const { bits, seconds } = this.airtime(raw.length, shield);
    this.emit("tx_start", { seq: f.seq, type: f.type, airtime: r2(seconds), shield });
    this.air = { kind: "tx", start: performance.now(), dur: seconds * 1000 };
    await this.sleep(seconds * 1000);
    this.air = null;
    this.counters.frames_tx++;
    if (f.type === "DATA") this.counters.data_frames_tx++;
    if (f.type === "ACK") this.counters.acks_tx++;
    if (shield) this.counters.shield_frames_tx++;
    this.emit("frame_tx", {
      frame: describe(f), raw: hex(raw), bytes: raw.length, airtime: r2(seconds), duration: r2(seconds), retry, msg_id: msgId,
      injected: null, lost_in_simulation: false, shield, bits_on_air: bits,
      text: `Sent ${f.type} seq ${f.seq} to node ${f.dst}` + (retry ? ` (retransmission ${retry})` : ""),
    });
  }

  /** The peer's frame crosses the air to us and is decoded: intact, repaired or damaged. */
  private async receive(f: MockFrame, outcome: "ok" | "damaged", shield = this.shield) {
    const clean = build(f);
    const { seconds } = this.airtime(clean.length, shield);
    this.emit("preamble", { score: r2(rand(0.7, 0.92)), shield, text: "Frame start detected" });
    this.air = { kind: "rx", start: performance.now(), dur: seconds * 1000 };
    await this.sleep(seconds * 1000);
    this.air = null;

    const nbits = clean.length * 8;
    const noisy = this.noisy();
    const clicks = noisy && this.noise.kind === "clicks";
    // hiss lowers every bit a little; a click leaves the rest clean and ruins single bits
    const conf = Array.from({ length: nbits }, () => r2(noisy && !clicks ? rand(0.66, 0.97) : rand(0.88, 0.99)));
    const pick = (n: number, from: number) => {
      // isolated positions: no two within a byte of each other
      const out: number[] = [];
      for (let guard = 0; out.length < n && guard < 400; guard++) {
        const i = Math.floor(rand(from, nbits));
        if (out.every((j) => Math.abs(j - i) > 8)) out.push(i);
      }
      return out.sort((a, b) => a - b);
    };
    if (noisy && !clicks) for (const i of pick(5, 0)) conf[i] = r2(rand(0.55, 0.62));
    const snr = r2(noisy ? 22 - this.noise.level * 19 + rand(-1, 1) : rand(23, 27));
    const common = { score: r2(rand(0.72, 0.9)), shield, snr_db: snr };
    this.heard = Date.now() / 1000;

    if (outcome === "damaged") {
      const raw = clean.slice();
      const flipped = pick(clicks ? (f.payload.length ? 3 : 2) : 1, f.payload.length ? 72 : 16);
      for (const i of flipped) {
        raw[i >> 3] ^= 0x80 >> (i & 7);
        conf[i] = r2(rand(0.5, 0.56));
      }
      const got = (raw[raw.length - 2] << 8) | raw[raw.length - 1];
      const calc = crc16(raw.subarray(1, raw.length - 2));
      const error = `CRC mismatch (received ${hex16(got)}, calculated ${hex16(calc)})`;
      this.counters.crc_failures++;
      this.emit("frame_rx", {
        ...common, ok: false, error, raw: hex(raw), bits_repaired: 0, repaired_at: [], crc_rx: hex16(got), crc_calc: hex16(calc), bit_conf: conf,
        frame: { ...describe(f, raw.subarray(9, raw.length - 2)), damaged: true }, text: `Frame rejected: ${error}`,
      });
      return;
    }

    const repaired = shield && noisy ? pick(Math.round(clicks ? rand(2, 4) : rand(0, 1.4)), 8) : [];
    for (const i of repaired) conf[i] = r2(rand(0.5, 0.58));
    // the decoder also fixes check bits, which are counted but have no place in the decoded frame
    const fixed = repaired.length + (shield && noisy ? Math.round(clicks ? rand(1, 2) : rand(0, 1)) : 0);
    const crc = hex16((clean[clean.length - 2] << 8) | clean[clean.length - 1]);
    this.counters.frames_rx_ok++;
    if (f.type === "ACK") this.counters.acks_rx++;
    if (shield) this.counters.shield_frames_rx++;
    this.counters.bits_repaired += fixed;
    if (repaired.length) this.counters.frames_repaired++;
    const forMe = f.dst === NODE || f.dst === 255;
    this.emit("frame_rx", {
      ...common, ok: true, frame: describe(f), raw: hex(clean), confidence: r2(conf.reduce((a, b) => a + b, 0) / nbits), for_me: forMe,
      bits_repaired: fixed, repaired_at: repaired, crc_rx: crc, crc_calc: crc, bit_conf: conf,
      text: `Received ${f.type} seq ${f.seq} from node ${f.src} - CRC OK` + (fixed ? ` (Noise Shield repaired ${fixed} bits)` : ""),
    });
  }

  private pieces(text: string): { payload: Uint8Array; flags: number }[] {
    const data = new TextEncoder().encode(text);
    const out: { payload: Uint8Array; flags: number }[] = [];
    for (let i = 0; i < Math.max(1, data.length); i += MAX_PAYLOAD) out.push({ payload: data.subarray(i, i + MAX_PAYLOAD), flags: 0 });
    out[0].flags |= F_FIRST;
    out[out.length - 1].flags |= F_LAST;
    return out;
  }

  /** This node sends a message: stop-and-wait, one frame at a time. */
  private sendMessage(text: string, dst: number) {
    const msgId = ++this.msgId;
    const parts = this.pieces(text);
    const bytes = new TextEncoder().encode(text).length;
    this.counters.messages_sent++;
    this.emit("message_status", {
      msg_id: msgId, dst, status: "queued", frames: parts.length, message: text, encrypted: false, via: dst,
      text: `Message #${msgId} to node ${dst} queued (${bytes} bytes, ${parts.length} frame(s))`,
    });
    return this.job(async () => {
      const t0 = performance.now();
      this.emit("message_status", { msg_id: msgId, dst, status: "sending", frames: parts.length, via: dst, text: `Sending message #${msgId} with Stop-and-Wait + CSMA` });
      for (let index = 0; index < parts.length; index++) {
        const frame: MockFrame = { type: "DATA", flags: parts[index].flags, src: NODE, dst, seq: this.nextSeq(), payload: parts[index].payload };
        let acked = false;
        for (let attempt = 0; attempt <= 5 && !acked; attempt++) {
          if (attempt) {
            this.counters.retransmissions++;
            this.emit("retransmit", { seq: frame.seq, attempt: attempt + 1, msg_id: msgId, text: `Retransmitting frame seq ${frame.seq} (attempt ${attempt + 1})` });
          }
          const shield = this.shield;
          await this.transmit(frame, attempt, msgId, shield);
          const waitFrom = performance.now();
          const rto = this.rto() * 1000;
          // the repeatable air: what the room does to a plain frame under generated noise
          const plan = this.noise.kind === "clicks" ? ["lost", "receipt-damaged", "lost"] : ["receipt-damaged"];
          const fate = !this.noisy() || shield ? "ok" : plan[attempt] ?? "ok";
          if (fate !== "lost") {
            await this.sleep(280);
            await this.receive({ type: "ACK", flags: 0, src: dst, dst: NODE, seq: frame.seq, payload: new Uint8Array() }, fate === "ok" ? "ok" : "damaged", shield);
          }
          if (fate === "ok") {
            acked = true;
            this.emit("message_progress", { msg_id: msgId, done: index + 1, total: parts.length, text: `Frame ${index + 1}/${parts.length} acknowledged (seq ${frame.seq})` });
          } else {
            await this.sleep(Math.max(150, rto - (performance.now() - waitFrom)));
            this.counters.timeouts++;
            this.emit("timeout", { seq: frame.seq, msg_id: msgId, attempt: attempt + 1, waited: r2(rto / 1000), text: `No ACK for seq ${frame.seq} after ${(rto / 1000).toFixed(1)} s - timeout` });
            await this.sleep(500);
          }
        }
        if (!acked) {
          this.counters.messages_failed++;
          const s = (performance.now() - t0) / 1000;
          this.emit("message_status", { msg_id: msgId, dst, status: "failed", seconds: Math.round(s * 10) / 10, text: `Message #${msgId} FAILED: no ACK after 5 retries` });
          return;
        }
        await this.sleep(250);
      }
      const s = (performance.now() - t0) / 1000;
      this.counters.messages_delivered++;
      this.emit("message_status", { msg_id: msgId, dst, status: "delivered", seconds: Math.round(s * 10) / 10, text: `Message #${msgId} DELIVERED to node ${dst} in ${s.toFixed(1)} s (every frame acknowledged)` });
    });
  }

  /** The other laptop sends us a message. Under plain noise its first try arrives damaged. */
  private peerMessage(text: string) {
    return this.job(async () => {
      for (const part of this.pieces(text)) {
        const frame: MockFrame = { type: "DATA", flags: part.flags, src: PEER, dst: NODE, seq: this.nextPeerSeq(), payload: part.payload };
        const shield = this.shield;
        for (let bad = this.noisy() && !shield ? (this.noise.kind === "clicks" ? 2 : 1) : 0; bad > 0; bad--) {
          await this.receive(frame, "damaged", shield);
          await this.sleep(this.rto() * 1000); // the peer waits for a receipt that never comes, then sends again
        }
        await this.receive(frame, "ok", shield);
        await this.sleep(260);
        await this.transmit({ type: "ACK", flags: 0, src: NODE, dst: PEER, seq: frame.seq, payload: new Uint8Array() }, 0, null, shield);
        await this.sleep(200);
      }
      this.counters.messages_received++;
      this.emit("message_rx", { origin: PEER, message: text, length: new TextEncoder().encode(text).length, encrypted: false, hops: 1, text: `Message from node ${PEER} delivered: '${text}'` });
    });
  }

  // ── the looping demo story ────────────────────────────────────────────────
  private async quiet(ms: number) {
    // wait out anything on air, and stand back while a person is driving
    for (;;) {
      await this.sleep(ms);
      if (this.pending === 0 && Date.now() - this.lastTouch > IDLE_BEFORE_AUTOPILOT_MS) return;
      ms = 1000;
    }
  }

  private async story() {
    this.lastTouch = 0;
    await this.sleep(1600);
    for (;;) {
      // 1. a quiet room: sent, receipt, delivered
      await this.quiet(600);
      await this.sendMessage("HELLO", PEER);
      await this.quiet(1400);
      await this.peerMessage("Loud and clear");
      // 2. noise: damaged frames, the seal check, timeouts, resends
      await this.quiet(2600);
      this.setNoise(true, 0.25, "clicks");
      await this.quiet(2200);
      await this.sendMessage("Can you hear me?", PEER);
      await this.quiet(1600);
      await this.peerMessage("What?");
      // 3. the shield: same noise, first try
      await this.quiet(2600);
      this.setShield(true);
      await this.quiet(2600);
      await this.sendMessage("Shield on", PEER);
      await this.quiet(1400);
      await this.peerMessage("Got it");
      await this.quiet(5000);
      this.setNoise(false, 0.25);
      await this.quiet(900);
      this.setShield(false);
      await this.quiet(3500);
    }
  }

  // ── what the microphone "hears", 12 times a second ────────────────────────
  private spectrum() {
    const now = performance.now();
    const air = this.air && now - this.air.start < this.air.dur + 60 ? this.air : null;
    const bins = new Array<number>(BINS);
    const step = (F_HI - F_LO) / BINS;
    const bin = (hz: number) => Math.max(0, Math.min(BINS - 1, Math.floor((hz - F_LO) / step)));
    const click = this.noise.on && this.noise.kind === "clicks" && Math.random() < 0.16 + this.noise.level * 0.5 ? 20 + this.noise.level * 26 : 0;
    for (let i = 0; i < BINS; i++) {
      const f = F_LO + (i + 0.5) * step;
      let db = -76 + rand(0, 6);
      if (f < 1300) db += 12 * (1 - (f - F_LO) / 800) + rand(0, 4); // the room's own rumble
      if (this.noise.on && this.noise.kind === "hiss") {
        const edge = Math.min(1, Math.max(0, (f - 1500) / 400)) * Math.min(1, Math.max(0, (4700 - f) / 400));
        db += edge * (12 + this.noise.level * 34 + rand(-4, 4));
      }
      if (click) db += click + rand(-4, 4); // a click is short and wide: every bin jumps at once
      bins[i] = db;
    }
    const b0 = bin(FREQ0);
    const b1 = bin(FREQ1);
    if (air) {
      const t = (now - air.start) / 1000;
      const top = air.kind === "tx" ? -8 - (1 - this.volume) * 10 : -21;
      const put = (i: number, db: number) => {
        if (i >= 0 && i < BINS) bins[i] = Math.max(bins[i], db);
      };
      if (t < 0.17) {
        const c = bin(1000 + Math.min(1, t / 0.17) * 3000); // the start chirp sliding up
        put(c, top);
        put(c - 1, top - 9);
        put(c + 1, top - 9);
      } else {
        // each 83 ms window holds about four bits; one tone usually dominates it
        const lean = Math.random() < 0.5 ? 0 : 1;
        const d0 = top - (lean === 0 ? rand(0, 2) : rand(6, 13));
        const d1 = top - (lean === 1 ? rand(0, 2) : rand(6, 13));
        put(b0, d0);
        put(b1, d1);
        for (const o of [-1, 1]) {
          put(b0 + o, d0 - 20);
          put(b1 + o, d1 - 20);
        }
      }
    }
    const rest = bins.filter((_, i) => Math.abs(i - b0) > 1 && Math.abs(i - b1) > 1).sort((a, b) => a - b);
    const level = air ? (air.kind === "tx" ? 0.3 + this.volume * 0.3 : 0.13) + rand(-0.02, 0.02) : 0.012 + (this.noise.on ? this.noise.level * 0.14 : 0) + rand(0, 0.006);
    this.emit("spectrum", {
      bins: bins.map((v) => Math.round(Math.max(-90, Math.min(0, v)) * 10) / 10), f_lo: F_LO, f_hi: F_HI, level: Math.round(level * 1000) / 1000,
      tone0: Math.round(bins[b0] * 10) / 10, tone1: Math.round(bins[b1] * 10) / 10, noise_db: Math.round(rest[rest.length >> 1] * 10) / 10,
      transmitting: air?.kind === "tx",
    });
  }
}
