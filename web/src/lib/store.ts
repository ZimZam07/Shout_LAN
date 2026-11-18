import { useSyncExternalStore } from "react";
import type { Command, ConnState, EngineEvent, FrameInfo, SpectrumEvent, Status, Transport } from "./types";
import { bool, num, numList, obj, str } from "./types";

/* One store for the whole console. Engine events come in through `ingest`;
 * React reads slices with `useStore`. The `spectrum` event (12 a second) never
 * touches React state: it lands in `air`, which the stage canvas reads on its
 * own clock. Every list is bounded, so twenty minutes of demo cost nothing.
 *
 * The stage (`scene`) only ever shows what a LIVE event said happened:
 * replayed events fill the lists but animate nothing. */

const MAX_FRAMES = 160;
const MAX_MSGS = 60;
const MAX_LOG = 300;

export type MsgStatus = "queued" | "sending" | "delivered" | "failed" | "received";
export interface Msg {
  key: string;
  dir: "out" | "in";
  id?: number;
  peer: number;
  text: string;
  status: MsgStatus;
  frames?: number;
  done: number;
  tries: number;
  seconds?: number;
  time: number;
}

export interface FrameRec {
  key: number;
  dir: "tx" | "rx";
  ok: boolean;
  time: number;
  info?: FrameInfo;
  raw?: string;
  retry: number;
  msgId?: number;
  shield?: boolean;
  bitsRepaired: number;
  repairedAt?: number[];
  crcRx?: string;
  crcCalc?: string;
  bitConf?: number[];
  snr?: number;
  error?: string;
  airtime?: number;
  bitsOnAir?: number;
  score?: number;
}

export type Tone = "idle" | "go" | "wait" | "ok" | "bad" | "shield" | "noise";
export interface LogEntry {
  key: number;
  time: number;
  text: string;
  tone: Tone;
}

export type CaptionIcon = "ear" | "send" | "back" | "wait" | "seal" | "broken" | "again" | "shield" | "noise" | "ticks" | "inbox" | "off";
export interface Caption {
  key: number;
  tone: Tone;
  icon: CaptionIcon;
  plain: string;
  tech: string;
}

export interface OutParcel {
  key: number;
  type: string;
  seq?: number;
  text?: string;
  part?: string;
  airtime: number;
  start: number;
  retry: number;
  shield: boolean;
  landed: boolean;
}
export interface InParcel {
  key: number;
  start: number;
  shield: boolean;
  result?: { ok: boolean; type?: string; text?: string; repaired: number; crcRx?: string; crcCalc?: string };
}
export interface Scene {
  out: OutParcel | null;
  inc: InParcel | null;
  wait: { key: number; seq?: number; rto: number } | null;
  caption: Caption;
  flash: { key: number; kind: "shield-on" | "shield-off" | "noise-on" | "noise-off" } | null;
}

export interface State {
  conn: ConnState;
  mock: boolean;
  everLive: boolean;
  status: Status | null;
  messages: Msg[];
  frames: FrameRec[];
  log: LogEntry[];
  scene: Scene;
  selected: number | null;
  notice: string | null;
}

const IDLE: Omit<Caption, "key"> = { tone: "idle", icon: "ear", plain: "Quiet. Listening for a message.", tech: "microphone open · waiting for a start chirp" };

let uid = 1;
const next = () => uid++;

function fresh(): Pick<State, "messages" | "frames" | "log" | "scene" | "selected"> {
  return { messages: [], frames: [], log: [], selected: null, scene: { out: null, inc: null, wait: null, flash: null, caption: { key: next(), ...IDLE } } };
}

let state: State = { conn: "connecting", mock: false, everLive: false, status: null, notice: null, ...fresh() };
const listeners = new Set<() => void>();
const set = (patch: Partial<State>) => {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
};
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
export function useStore<T>(select: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => select(state));
}
export const getState = () => state;

// ── the air: latest spectrum, outside React ─────────────────────────────────
export const air: { latest: SpectrumEvent | null; at: number; listeners: Set<(s: SpectrumEvent) => void> } = { latest: null, at: 0, listeners: new Set() };

function ingestSpectrum(e: EngineEvent) {
  const bins = numList(e.bins);
  if (!bins || bins.length === 0) return;
  const s: SpectrumEvent = {
    bins, f_lo: num(e.f_lo) ?? 500, f_hi: num(e.f_hi) ?? 6000, level: num(e.level) ?? 0,
    tone0: num(e.tone0) ?? -90, tone1: num(e.tone1) ?? -90, noise_db: num(e.noise_db) ?? -90, transmitting: bool(e.transmitting) ?? false,
  };
  air.latest = s;
  air.at = performance.now();
  air.listeners.forEach((l) => l(s));
}

// ── plain words ─────────────────────────────────────────────────────────────
/** The everyday word for a frame type: the parcel metaphor, used everywhere. */
export function kindWord(type?: string): string {
  switch (type) {
    case "DATA":
    case "DATA-SR":
      return "Message";
    case "ACK":
    case "SACK":
      return "Receipt";
    case "HELLO":
      return "Hello";
    case "PING":
      return "Ping";
    case "PONG":
      return "Ping reply";
    default:
      return type ?? "Parcel";
  }
}
const ordinal = (n: number) => (n === 2 ? "2nd" : n === 3 ? "3rd" : `${n}th`);
export const tryWord = (retry: number) => `${ordinal(retry + 1)} try`;
const secs = (v?: number) => (v === undefined ? "" : `${v.toFixed(1)} s`);

export function payloadText(info?: FrameInfo): string | undefined {
  const hex = info?.payload_hex?.trim();
  if (!hex) return undefined;
  const bytes = hex.split(/\s+/).map((h) => parseInt(h, 16));
  if (bytes.some(Number.isNaN)) return undefined;
  // printable ASCII as is, anything else as a dot: a damaged payload must not pretend to be clean text
  return bytes.map((b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : "·")).join("");
}

// ── timers the scene needs (a landed parcel leaves the stage) ───────────────
const timers = new Map<string, ReturnType<typeof setTimeout>>();
function later(name: string, ms: number, fn: () => void) {
  const old = timers.get(name);
  if (old) clearTimeout(old);
  timers.set(name, setTimeout(() => {
    timers.delete(name);
    fn();
  }, ms));
}
const scene = (patch: Partial<Scene>) => set({ scene: { ...state.scene, ...patch } });
function caption(c: Omit<Caption, "key">, holdMs = 7000) {
  scene({ caption: { key: next(), ...c } });
  later("caption", holdMs, () => {
    if (!state.scene.out && !state.scene.inc && !state.scene.wait) scene({ caption: { key: next(), ...IDLE } });
    else later("caption", 2500, () => scene({ caption: { key: next(), ...IDLE } }));
  });
}

function logTone(e: EngineEvent): Tone {
  switch (e.event) {
    case "frame_rx":
      return e.ok === false ? "bad" : (num(e.bits_repaired) ?? 0) > 0 ? "shield" : "ok";
    case "timeout":
    case "error":
    case "ping_timeout":
      return "bad";
    case "retransmit":
    case "duplicate":
    case "channel_busy":
    case "backoff":
      return "wait";
    case "message_rx":
      return "ok";
    case "message_status":
      return e.status === "delivered" ? "ok" : e.status === "failed" ? "bad" : "go";
    case "shield_set":
      return "shield";
    case "noise_set":
      return "noise";
    case "tx_start":
    case "frame_tx":
      return "go";
    default:
      return "idle";
  }
}

let pendingRetry: { seq?: number; attempt: number } | null = null;

function patchMsg(id: number | undefined, fn: (m: Msg) => Msg): Msg[] {
  return state.messages.map((m) => (m.dir === "out" && m.id === id ? fn(m) : m));
}
const cap = <T,>(list: T[], max: number) => (list.length > max ? list.slice(list.length - max) : list);

/** Every engine event passes through here. */
export function ingest(e: EngineEvent) {
  if (e.event === "spectrum") return ingestSpectrum(e);
  if (e.event === "replay_done") return;
  if (e.event === "cleared") {
    // someone cleared the run (any tab): start from empty, like the server did
    set(fresh());
    void refreshStatus();
  }
  const live = e.replay !== true;
  const time = num(e.time) ?? Date.now() / 1000;
  const me = state.status?.node_id;
  const patch: Partial<State> = {};

  const text = str(e.text);
  if (text && e.event !== "tx_start") patch.log = cap([...state.log, { key: next(), time, text, tone: logTone(e) }], MAX_LOG);

  const info = obj(e.frame) as FrameInfo | undefined;
  switch (e.event) {
    case "message_status": {
      const id = num(e.msg_id);
      const status = str(e.status) as MsgStatus | undefined;
      if (id === undefined || !status) break;
      if (status === "queued" || !state.messages.some((m) => m.dir === "out" && m.id === id)) {
        if (state.messages.some((m) => m.dir === "out" && m.id === id)) break;
        const m: Msg = { key: `o${id}-${next()}`, dir: "out", id, peer: num(e.dst) ?? 0, text: str(e.message) ?? `Message #${id}`, status, frames: num(e.frames), done: 0, tries: 0, time };
        patch.messages = cap([...state.messages, m], MAX_MSGS);
      } else {
        patch.messages = patchMsg(id, (m) => ({ ...m, status, frames: num(e.frames) ?? m.frames, seconds: num(e.seconds) ?? m.seconds, done: status === "delivered" ? m.frames ?? m.done : m.done }));
      }
      break;
    }
    case "message_progress":
      patch.messages = patchMsg(num(e.msg_id), (m) => ({ ...m, done: num(e.done) ?? m.done, frames: num(e.total) ?? m.frames }));
      break;
    case "retransmit":
      patch.messages = patchMsg(num(e.msg_id), (m) => ({ ...m, tries: m.tries + 1 }));
      break;
    case "message_rx": {
      const m: Msg = { key: `i${next()}`, dir: "in", peer: num(e.origin) ?? 0, text: str(e.message) ?? "", status: "received", done: 0, tries: 0, time };
      patch.messages = cap([...state.messages, m], MAX_MSGS);
      break;
    }
    case "frame_tx":
    case "frame_rx": {
      const rx = e.event === "frame_rx";
      const rec: FrameRec = {
        key: next(), dir: rx ? "rx" : "tx", ok: rx ? e.ok !== false : true, time, info, raw: str(e.raw), retry: num(e.retry) ?? 0, msgId: num(e.msg_id),
        shield: bool(e.shield), bitsRepaired: num(e.bits_repaired) ?? 0, repairedAt: numList(e.repaired_at), crcRx: str(e.crc_rx), crcCalc: str(e.crc_calc),
        bitConf: numList(e.bit_conf), snr: num(e.snr_db), error: str(e.error), airtime: num(e.airtime), bitsOnAir: num(e.bits_on_air), score: num(e.score),
      };
      patch.frames = cap([...state.frames, rec], MAX_FRAMES);
      break;
    }
    case "shield_set":
      if (state.status && bool(e.value) !== undefined) patch.status = { ...state.status, shield: bool(e.value) };
      break;
    case "noise_set":
      if (state.status) patch.status = { ...state.status, noise: { on: bool(e.on) ?? false, level: num(e.level) ?? state.status.noise?.level ?? 0.5 } };
      break;
    case "setting":
      if (state.status && e.name === "volume" && num(e.value) !== undefined) patch.status = { ...state.status, volume: num(e.value) };
      break;
  }
  if (Object.keys(patch).length) set(patch);
  if (live) direct(e, info, me);
}

/** The stage: one live event, one visible moment. */
function direct(e: EngineEvent, info: FrameInfo | undefined, me: number | undefined) {
  const st = state.status;
  switch (e.event) {
    case "retransmit": {
      const attempt = num(e.attempt) ?? 1;
      pendingRetry = { seq: num(e.seq), attempt };
      caption({ tone: "wait", icon: "again", plain: `Sending it again. ${tryWord(attempt)}.`, tech: `retransmission · attempt ${attempt + 1} · seq ${num(e.seq) ?? "?"}` });
      break;
    }
    case "tx_start": {
      const type = str(e.type) ?? "DATA";
      const seq = num(e.seq);
      const airtime = num(e.airtime) ?? 2.5;
      const isData = type.startsWith("DATA");
      const retry = isData && pendingRetry && (pendingRetry.seq === undefined || pendingRetry.seq === seq) ? pendingRetry.attempt : 0;
      pendingRetry = null;
      const msg = isData ? [...state.messages].reverse().find((m) => m.dir === "out" && m.status === "sending") : undefined;
      const part = msg?.frames && msg.frames > 1 ? `part ${Math.min(msg.done + 1, msg.frames)} of ${msg.frames}` : undefined;
      // our receipts answer in the format of the frame they answer; our own frames use our switch
      const shield = isData ? st?.shield === true : state.scene.inc?.shield ?? st?.shield === true;
      scene({ out: { key: next(), type, seq, text: msg?.text, part, airtime, start: performance.now(), retry, shield, landed: false }, wait: null });
      const tech = `${type} frame · seq ${seq ?? "?"} · ${secs(airtime)} on air`;
      if (isData) caption({ tone: "go", icon: "send", plain: retry ? `Sending it again. ${tryWord(retry)}.` : "Sending your message as sound.", tech: retry ? `retransmission · ${tech}` : tech }, airtime * 1000 + 4000);
      else if (type === "ACK" || type === "SACK") caption({ tone: "go", icon: "back", plain: "Sending a receipt back.", tech }, airtime * 1000 + 4000);
      else caption({ tone: "go", icon: "send", plain: `Sending a ${kindWord(type).toLowerCase()}.`, tech }, airtime * 1000 + 4000);
      break;
    }
    case "frame_tx": {
      const out = state.scene.out;
      const type = info?.type ?? out?.type ?? "";
      const isData = type.startsWith("DATA");
      const rto = st?.rto;
      scene({
        out: out ? { ...out, landed: true, shield: bool(e.shield) ?? out.shield } : null,
        wait: isData && rto ? { key: next(), seq: info?.seq, rto } : null,
      });
      later("out", 700, () => scene({ out: null }));
      if (isData) caption({ tone: "wait", icon: "wait", plain: "Sent. Waiting for the receipt.", tech: `waiting for ACK seq ${info?.seq ?? "?"}` + (rto ? ` · gives up after ${secs(rto)}` : "") }, 9000);
      break;
    }
    case "preamble": {
      scene({ inc: { key: next(), start: performance.now(), shield: bool(e.shield) ?? false } });
      later("inc", 12000, () => scene({ inc: null })); // nothing decoded: let it go
      if (!state.scene.wait) caption({ tone: "go", icon: "ear", plain: "Hearing something…", tech: `start chirp detected` + (num(e.score) !== undefined ? ` · score ${num(e.score)}` : "") }, 9000);
      break;
    }
    case "frame_rx": {
      const ok = e.ok !== false;
      if (ok && info?.src !== undefined && info.src === me) break; // our own echo
      const repaired = num(e.bits_repaired) ?? 0;
      const type = info?.type;
      const crcRx = str(e.crc_rx);
      const crcCalc = str(e.crc_calc);
      const prior = state.scene.inc;
      const isAck = type === "ACK" || type === "SACK";
      const result = { ok, type, text: type?.startsWith("DATA") ? payloadText(info) : undefined, repaired, crcRx, crcCalc };
      scene({
        inc: { key: prior?.key ?? next(), start: prior?.start ?? performance.now() - 1200, shield: bool(e.shield) ?? prior?.shield ?? false, result },
        wait: ok && isAck ? null : state.scene.wait,
      });
      later("inc", ok ? 2600 : 3400, () => scene({ inc: null }));
      const from = info?.src !== undefined ? ` from node ${info.src}` : "";
      const crc = crcRx && crcCalc ? (ok ? `CRC ${crcRx} = ${crcCalc}` : `CRC mismatch · received ${crcRx}, calculated ${crcCalc}`) : ok ? "CRC OK" : str(e.error) ?? "CRC failed";
      const head = `${type ?? "frame"}${info?.seq !== undefined ? ` seq ${info.seq}` : ""}${from}`;
      if (!ok) caption({ tone: "bad", icon: "broken", plain: "Arrived damaged. Thrown away.", tech: `${head} · ${crc}` }, 9000);
      else if (repaired > 0) {
        const code = st?.shield_info?.code;
        caption({ tone: "shield", icon: "shield", plain: `The shield repaired ${repaired} damaged bit${repaired === 1 ? "" : "s"}.`, tech: `${head} · ${code ? `${code} corrected ${repaired}` : `${repaired} corrected`} · ${crc}` }, 9000);
      } else if (isAck) caption({ tone: "ok", icon: "seal", plain: "Receipt heard. Seal intact.", tech: `${head} · ${crc}` });
      else if (type?.startsWith("DATA")) caption({ tone: "ok", icon: "seal", plain: "A message arrived. Seal intact.", tech: `${head} · ${crc}` });
      else caption({ tone: "ok", icon: "seal", plain: `Heard a ${kindWord(type).toLowerCase()}${from}.`, tech: `${head} · ${crc}` });
      break;
    }
    case "timeout": {
      scene({ wait: null });
      caption({ tone: "bad", icon: "wait", plain: "No valid receipt in time.", tech: `timeout${num(e.waited) !== undefined ? ` after ${secs(num(e.waited))}` : ""} · seq ${num(e.seq) ?? "?"}` }, 9000);
      break;
    }
    case "message_status": {
      if (e.status === "delivered") {
        scene({ wait: null });
        later("delivered", 1300, () => caption({ tone: "ok", icon: "ticks", plain: "Delivered.", tech: `message #${num(e.msg_id) ?? "?"} acknowledged${num(e.seconds) !== undefined ? ` in ${secs(num(e.seconds))}` : ""}` }));
      } else if (e.status === "failed") {
        scene({ wait: null });
        caption({ tone: "bad", icon: "off", plain: "Gave up. Not delivered.", tech: str(e.text) ?? "no ACK after every retry" }, 10000);
      }
      break;
    }
    case "duplicate":
      caption({ tone: "wait", icon: "again", plain: "Same parcel twice. Kept one.", tech: `duplicate seq ${num(e.seq) ?? "?"}${num(e.src) !== undefined ? ` from node ${num(e.src)}` : ""}` });
      break;
    case "shield_set": {
      const on = e.value === true;
      const si = st?.shield_info;
      const what = [si?.code, si?.filter_hz ? `band-pass ${si.filter_hz[0]}–${si.filter_hz[1]} Hz` : undefined].filter(Boolean).join(" + ");
      scene({ flash: { key: next(), kind: on ? "shield-on" : "shield-off" } });
      caption({ tone: on ? "shield" : "idle", icon: on ? "shield" : "off", plain: on ? "Shield on: parcels repair themselves." : "Shield off: parcels travel unprotected.", tech: on ? what || "forward error correction on" : "plain frames · a damaged frame must be sent again" }, 8000);
      break;
    }
    case "noise_set": {
      const on = e.on === true;
      const sim = st?.mode === "simulated" ? "simulated " : "";
      scene({ flash: { key: next(), kind: on ? "noise-on" : "noise-off" } });
      caption({ tone: on ? "noise" : "idle", icon: on ? "noise" : "ear", plain: on ? "Generated noise on. The room is loud." : "Generated noise off.", tech: on ? `${sim}interference generator · level ${Math.round((num(e.level) ?? 0) * 100)}%` : `${sim}interference generator stopped` }, 8000);
      break;
    }
  }
}

// ── wiring ──────────────────────────────────────────────────────────────────
let transport: Transport | null = null;
let poll: ReturnType<typeof setInterval> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
const REFRESH_ON = new Set(["frame_tx", "frame_rx", "message_status", "message_rx", "timeout", "retransmit", "shield_set", "noise_set", "setting", "engine_started"]);

export async function refreshStatus() {
  if (!transport) return;
  try {
    const status = await transport.status();
    set({ status });
  } catch {
    /* the stream's own state says we are disconnected */
  }
}

export function connect(t: Transport) {
  disconnect();
  transport = t;
  set({ mock: t.mock, conn: "connecting" });
  t.start(
    (e) => {
      ingest(e);
      if (REFRESH_ON.has(e.event) && e.replay !== true && !refreshTimer) {
        refreshTimer = setTimeout(() => {
          refreshTimer = null;
          void refreshStatus();
        }, 120);
      }
    },
    (conn) => {
      // a fresh connection replays the run from the start: begin from empty lists
      if (conn === "live") set({ conn, everLive: true, ...fresh() });
      else set({ conn });
      if (conn === "live") void refreshStatus();
    },
  );
  void refreshStatus();
  poll = setInterval(() => void refreshStatus(), 2000);
}

export function disconnect() {
  transport?.stop();
  transport = null;
  if (poll) clearInterval(poll);
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = null;
  timers.forEach(clearTimeout);
  timers.clear();
}

export async function command(c: Command): Promise<boolean> {
  if (!transport) return false;
  const reply = await transport.cmd(c);
  if (!reply.ok) {
    set({ notice: reply.error ? `The engine refused: ${reply.error}` : "The engine refused that command" });
    later("notice", 5000, () => set({ notice: null }));
    return false;
  }
  if (c.cmd === "clear") set(fresh());
  void refreshStatus();
  return true;
}

export const select = (key: number | null) => set({ selected: key });
export const dismissNotice = () => set({ notice: null });
