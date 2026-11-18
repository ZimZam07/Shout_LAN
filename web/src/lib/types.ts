/* The data contract, docs/DASHBOARD_API.md. Everything the engine may leave out
 * (an older build) is optional here, and every reader copes without it. */

export type Mode = "acoustic" | "pair" | "simulated";

export interface ShieldInfo {
  code?: string;
  rate?: number;
  /** the receive band-pass used to find the start chirp, for both formats */
  filter_hz?: [number, number];
  airtime_factor?: number;
  decoder?: unknown;
  interleaving?: unknown;
}

export interface Status {
  node_id: number;
  channel?: string;
  channel_label?: string;
  running?: boolean;
  transmitting?: boolean;
  protocol?: string;
  nodes?: Record<string, { last_heard?: number; score?: number }>;
  stats?: {
    source?: string;
    counters?: Record<string, number>;
    delivery_ratio?: number | null;
    frame_loss?: number | null;
    goodput_bps?: number | null;
  };
  freq0?: number;
  freq1?: number;
  bit_time?: number;
  volume?: number;
  mic_level?: number;
  speaker_mode?: string;
  rto?: number;
  shield?: boolean;
  shield_info?: ShieldInfo;
  /** false when this engine build has no set_shield / set_noise (see Deviations). */
  shield_available?: boolean;
  noise_available?: boolean;
  /** `kind` is absent on engines with one kind of generated noise. */
  noise?: { on?: boolean; level?: number; kind?: string };
  mode?: Mode;
  peer?: number | null;
  http_port?: number;
  other_dashboards?: { node: number; url: string }[];
}

/** `Frame.describe()` in shout_lan/frame.py. */
export interface FrameInfo {
  type?: string;
  flags?: string[];
  src?: number;
  dst?: number;
  seq?: number;
  len?: number;
  origin?: number;
  final?: number;
  ttl?: number;
  payload_hex?: string;
  damaged?: boolean;
}

/** One SSE message. Fields beyond the common four depend on `event`. */
export interface EngineEvent {
  event: string;
  time?: number;
  node?: number;
  text?: string;
  replay?: boolean;
  [key: string]: unknown;
}

export interface SpectrumEvent {
  bins: number[];
  f_lo: number;
  f_hi: number;
  level: number;
  tone0: number;
  tone1: number;
  noise_db: number;
  transmitting: boolean;
}

export type Command =
  | { cmd: "send"; dst: number; text: string }
  | { cmd: "ping"; dst: number; count?: number }
  | { cmd: "hello" }
  | { cmd: "set_volume"; value: number }
  | { cmd: "set_shield"; value: boolean }
  | { cmd: "set_noise"; on: boolean; level?: number; kind?: string }
  | { cmd: "clear" };

export interface CmdReply {
  ok: boolean;
  error?: string;
}

export type ConnState = "connecting" | "live" | "down";

/** What the app talks to: the real engine or the mock one. */
export interface Transport {
  readonly mock: boolean;
  start(onEvent: (e: EngineEvent) => void, onConn: (c: ConnState) => void): void;
  stop(): void;
  status(): Promise<Status>;
  cmd(c: Command): Promise<CmdReply>;
}

// ── tolerant readers ────────────────────────────────────────────────────────
export const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
export const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);
export const bool = (v: unknown): boolean | undefined => (typeof v === "boolean" ? v : undefined);
export const numList = (v: unknown): number[] | undefined =>
  Array.isArray(v) && v.every((x) => typeof x === "number") ? (v as number[]) : undefined;
export const obj = (v: unknown): Record<string, unknown> | undefined =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
