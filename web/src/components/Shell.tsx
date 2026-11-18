import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, Eraser, FlaskConical, Radar, ScrollText, SlidersHorizontal, Timer, Unplug, Volume2, Wifi, X } from "lucide-react";
import { command, dismissNotice, useStore, type Tone } from "../lib/store";
import { useEscape } from "./hooks";

export function Mark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path d="M8 13v6M12 10v12M16 6v20M20 11v10M24 14v4" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

// ── the dock ────────────────────────────────────────────────────────────────
export function Dock({ onHome, onLog, logOpen }: { onHome: () => void; onLog: () => void; logOpen: boolean }) {
  const status = useStore((s) => s.status);
  const conn = useStore((s) => s.conn);
  const mock = useStore((s) => s.mock);
  const [tools, setTools] = useState(false);
  const closeTools = useCallback(() => setTools(false), []);
  const wrap = useRef<HTMLDivElement>(null);
  useEscape(tools, closeTools);
  useEffect(() => {
    if (!tools) return;
    const on = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setTools(false);
    };
    window.addEventListener("pointerdown", on);
    return () => window.removeEventListener("pointerdown", on);
  }, [tools]);

  const Conn = conn === "live" ? Wifi : Unplug;
  return (
    <div className="sl-dock-wrap" ref={wrap}>
      <nav className="sl-dock" aria-label="Console">
        <button type="button" className="sl-dock-mark" onClick={onHome} aria-label="Opening screen">
          <Mark />
        </button>
        <span className="sl-dock-id">
          <b>Shout LAN</b>
          <span>node {status?.node_id ?? "–"}{status?.protocol ? ` · ${status.protocol}` : ""}</span>
        </span>
        <span className={`sl-dock-conn is-${mock ? "mock" : conn}`} role="status">
          <Conn aria-hidden strokeWidth={2} />
          {mock ? "Mock engine" : conn === "live" ? "Live" : conn === "connecting" ? "Connecting" : "Disconnected"}
        </span>
        <button type="button" className={`sl-dock-item${logOpen ? " is-on" : ""}`} onClick={onLog} aria-pressed={logOpen}>
          <ScrollText aria-hidden strokeWidth={1.8} /> Log
        </button>
        <button type="button" className={`sl-dock-item${tools ? " is-on" : ""}`} onClick={() => setTools((v) => !v)} aria-expanded={tools}>
          <SlidersHorizontal aria-hidden strokeWidth={1.8} /> Tools
        </button>
      </nav>
      {tools && <Tools />}
    </div>
  );
}

// ── the rarely used things ──────────────────────────────────────────────────
function Tools() {
  const status = useStore((s) => s.status);
  const [vol, setVol] = useState<number | null>(null);
  const [armed, setArmed] = useState(false);
  const volume = vol ?? status?.volume ?? 0.5;
  const peer = status?.peer ?? null;
  const commit = (v: number) => {
    setVol(null);
    void command({ cmd: "set_volume", value: v });
  };
  return (
    <div className="sl-tools" role="dialog" aria-label="Tools">
      <button type="button" className="sl-tool" onClick={() => void command({ cmd: "hello" })}>
        <Radar aria-hidden strokeWidth={1.6} />
        <span>
          <b>Discover neighbours</b>
          <small>shout a hello and see who answers</small>
        </span>
      </button>
      <button type="button" className="sl-tool" disabled={peer === null} onClick={() => peer !== null && void command({ cmd: "ping", dst: peer, count: 2 })}>
        <Timer aria-hidden strokeWidth={1.6} />
        <span>
          <b>Ping{peer !== null ? ` node ${peer}` : ""}</b>
          <small>time a round trip, twice</small>
        </span>
      </button>
      <label className="sl-tool is-slider">
        <Volume2 aria-hidden strokeWidth={1.6} />
        <span>
          <b>Speaker volume · {Math.round(volume * 100)}%</b>
          <input
            type="range"
            min={0.05}
            max={1}
            step={0.05}
            value={volume}
            onChange={(e) => setVol(Number(e.target.value))}
            onPointerUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
            onKeyUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
            style={{ ["--fill" as string]: `${volume * 100}%` }}
          />
        </span>
      </label>
      <button
        type="button"
        className={`sl-tool${armed ? " is-armed" : ""}`}
        onClick={() => {
          if (!armed) return setArmed(true);
          setArmed(false);
          void command({ cmd: "clear" });
        }}
        onBlur={() => setArmed(false)}
      >
        <Eraser aria-hidden strokeWidth={1.6} />
        <span>
          <b>{armed ? "Press again to clear" : "Clear and reset"}</b>
          <small>forget this run: messages, parcels, counters</small>
        </span>
      </button>
      {(status?.other_dashboards ?? []).map((d) => (
        <a key={d.node} className="sl-tool" href={d.url} target="_blank" rel="noreferrer">
          <ExternalLink aria-hidden strokeWidth={1.6} />
          <span>
            <b>Open node {d.node}'s console</b>
            <small>{d.url.replace(/^https?:\/\//, "")}</small>
          </span>
        </a>
      ))}
    </div>
  );
}

// ── the log ─────────────────────────────────────────────────────────────────
const WORD: Record<Tone, string> = { idle: "note", go: "sent", wait: "wait", ok: "ok", bad: "fault", shield: "shield", noise: "noise" };
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });

export function Ticker({ onOpen }: { onOpen: () => void }) {
  const last = useStore((s) => s.log[s.log.length - 1]);
  return (
    <button type="button" className="sl-ticker" onClick={onOpen} aria-label="Open the log">
      <ScrollText aria-hidden strokeWidth={1.7} />
      {last ? (
        <span key={last.key} className={`sl-ticker-text is-${last.tone}`}>
          <time>{clock(last.time)}</time>
          {last.text}
        </span>
      ) : (
        <span className="sl-ticker-text">The engine's own account of every step appears here.</span>
      )}
      <em>Log</em>
    </button>
  );
}

export function LogDrawer({ onClose }: { onClose: () => void }) {
  const log = useStore((s) => s.log);
  const ref = useRef<HTMLOListElement>(null);
  useEscape(true, onClose);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);
  return (
    <aside className="sl-drawer is-log" aria-label="Event log">
      <header className="sl-drawer-head">
        <span className="sl-drawer-mark" aria-hidden="true">
          <ScrollText strokeWidth={1.8} />
        </span>
        <div>
          <h2>Event log</h2>
          <p>the engine's own words, oldest first · {log.length} lines</p>
        </div>
        <button type="button" className="sl-x" onClick={onClose} aria-label="Close">
          <X aria-hidden strokeWidth={1.8} />
        </button>
      </header>
      <ol className="sl-log" ref={ref}>
        {log.length === 0 && <li className="sl-note">Nothing yet.</li>}
        {log.map((l) => (
          <li key={l.key} className={`is-${l.tone}`}>
            <time>{clock(l.time)}</time>
            <i>{WORD[l.tone]}</i>
            <span>{l.text}</span>
          </li>
        ))}
      </ol>
    </aside>
  );
}

// ── truth in the corners ────────────────────────────────────────────────────
export function Banners() {
  const conn = useStore((s) => s.conn);
  const everLive = useStore((s) => s.everLive);
  const mock = useStore((s) => s.mock);
  const notice = useStore((s) => s.notice);
  return (
    <>
      {conn !== "live" && !mock && (
        <div className="sl-banner" role="alert">
          <Unplug aria-hidden strokeWidth={1.8} />
          <div>
            <b>{everLive ? "Disconnected from the engine" : "No engine is answering"}</b>
            <span>{everLive ? "Trying again every second. What you see is the last known state." : "Start it with python shout.py, or look around with mock data."}</span>
          </div>
          {!everLive && (
            <a className="sl-banner-act" href="?mock#/console">
              Open with mock data
            </a>
          )}
        </div>
      )}
      {notice && (
        <button type="button" className="sl-toast" onClick={dismissNotice} role="alert">
          {notice}
        </button>
      )}
      {mock && (
        <div className="sl-mock" role="note">
          <FlaskConical aria-hidden strokeWidth={1.8} />
          <span>
            <b>Mock data</b> scripted, not a measurement
          </span>
        </div>
      )}
    </>
  );
}
