import type { CmdReply, Command, ConnState, EngineEvent, Status, Transport } from "./types";

/* The real engine: GET /api/status, GET /api/events (Server-Sent Events: a
 * replay of the last 300 events, `replay_done`, then live) and POST /api/cmd.
 * All paths are relative, so the page works on whichever port serves it. */

const RETRY_MS = 1500;

export async function fetchStatus(timeoutMs = 2500): Promise<Status> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch("api/status", { signal: ctl.signal, cache: "no-store" });
    if (!res.ok) throw new Error(`status ${res.status}`);
    const body: unknown = await res.json();
    if (!body || typeof body !== "object" || typeof (body as Status).node_id !== "number") throw new Error("not a status object");
    return body as Status;
  } finally {
    clearTimeout(timer);
  }
}

export class LiveTransport implements Transport {
  readonly mock = false;
  private source: EventSource | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  start(onEvent: (e: EngineEvent) => void, onConn: (c: ConnState) => void) {
    this.stopped = false;
    const open = () => {
      if (this.stopped) return;
      onConn("connecting");
      const es = new EventSource("api/events");
      this.source = es;
      es.onopen = () => onConn("live");
      es.onmessage = (m) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(m.data);
        } catch {
          return; // one bad line must not take the stream down
        }
        if (parsed && typeof parsed === "object" && typeof (parsed as EngineEvent).event === "string") onEvent(parsed as EngineEvent);
      };
      es.onerror = () => {
        // The browser's own retry is silent and can stall on a dead engine;
        // close and reopen ourselves so the "disconnected" state is truthful.
        es.close();
        if (this.source === es) this.source = null;
        if (this.stopped) return;
        onConn("down");
        this.retry = setTimeout(open, RETRY_MS);
      };
    };
    open();
  }

  stop() {
    this.stopped = true;
    if (this.retry) clearTimeout(this.retry);
    this.source?.close();
    this.source = null;
  }

  status() {
    return fetchStatus();
  }

  async cmd(c: Command): Promise<CmdReply> {
    try {
      const res = await fetch("api/cmd", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(c) });
      const body: unknown = await res.json().catch(() => null);
      if (body && typeof body === "object" && typeof (body as CmdReply).ok === "boolean") return body as CmdReply;
      return { ok: false, error: `The engine answered ${res.status}` };
    } catch {
      return { ok: false, error: "The engine did not answer" };
    }
  }
}
