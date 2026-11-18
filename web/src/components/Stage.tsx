import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AudioLines, Check, CheckCheck, Ear, Hourglass, Inbox, Laptop, RotateCw, Send, ShieldCheck, ShieldOff, Stamp, Trash2, Undo2, X, type LucideIcon } from "lucide-react";
import { air, kindWord, tryWord, useStore, type Caption, type CaptionIcon, type InParcel, type OutParcel } from "../lib/store";
import { GEOM, startAirCanvas, type CanvasOpts } from "./airCanvas";
import { useReducedMotion } from "./hooks";

/* The air between two laptops. The canvas underneath is the sound itself
 * (airCanvas.ts); on top of it travel the parcels, one per frame, each riding
 * the real airtime the engine reported. The sentence in the middle says what
 * just happened in plain words, with the networking term beneath it. */

const ICONS: Record<CaptionIcon, LucideIcon> = {
  ear: Ear, send: Send, back: Undo2, wait: Hourglass, seal: Stamp, broken: Trash2, again: RotateCw, shield: ShieldCheck, noise: AudioLines, ticks: CheckCheck, inbox: Inbox, off: ShieldOff,
};
// an outgoing parcel slides from flush-left to flush-right, so it never leaves the stage
const X_ME = "3%";
const X_PEER = "97%";
// incoming parcels use the row under the lanes, like the other side of a road
const IN_FROM = "76%";
const IN_SLOW = "42%";
const IN_LAND = "30%";

function Seal({ state }: { state: "plain" | "ok" | "bad" }) {
  const Icon = state === "ok" ? Check : state === "bad" ? X : Stamp;
  return (
    <span className={`sl-seal is-${state}`} aria-hidden="true">
      <Icon strokeWidth={state === "plain" ? 1.6 : 2.6} />
    </span>
  );
}

function Out({ p, reduced }: { p: OutParcel; reduced: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;
    // ride the real airtime: the parcel reaches the far side as the last beep ends
    const a = el.animate([{ left: X_ME, transform: "translate(0%, -100%)" }, { left: X_PEER, transform: "translate(-100%, -100%)" }], { duration: Math.max(200, p.airtime * 1000), easing: "linear", fill: "forwards" });
    a.currentTime = Math.max(0, performance.now() - p.start);
    return () => a.cancel();
  }, [p.key, p.airtime, p.start, reduced]);
  const receipt = p.type === "ACK" || p.type === "SACK";
  return (
    <div ref={ref} className={`sl-parcel is-out${p.landed ? " is-gone" : ""}${p.shield ? " is-shield" : ""}${receipt ? " is-receipt" : ""}`} style={reduced ? { left: "50%" } : undefined}>
      <div className="sl-parcel-box">
        {p.retry > 0 && (
          <span className="sl-parcel-try">
            <RotateCw aria-hidden strokeWidth={2.4} /> {tryWord(p.retry)}
          </span>
        )}
        {p.shield && <ShieldCheck className="sl-parcel-shield" aria-hidden strokeWidth={2} />}
        <div className="sl-parcel-words">
          <span className="sl-parcel-text">{receipt ? "Receipt" : p.type.startsWith("DATA") ? p.text ?? "Message" : kindWord(p.type)}</span>
          <span className="sl-parcel-tech">
            {p.type}
            {p.seq !== undefined ? ` · seq ${p.seq}` : ""}
            {p.part ? ` · ${p.part}` : ""}
          </span>
        </div>
        <Seal state="plain" />
      </div>
    </div>
  );
}

function In({ p, reduced }: { p: InParcel; reduced: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const flight = useRef<Animation | null>(null);
  const [tossed, setTossed] = useState(false);
  const res = p.result;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;
    // we cannot know how long an incoming frame is until it ends, so it
    // approaches and slows; the landing below happens when the frame is decoded
    const a = el.animate([{ left: IN_FROM }, { left: IN_SLOW }], { duration: p.shield ? 3600 : 2300, easing: "cubic-bezier(0.25, 0.6, 0.35, 1)", fill: "forwards" });
    a.currentTime = Math.max(0, performance.now() - p.start);
    flight.current = a;
    return () => a.cancel();
  }, [p.key, p.shield, p.start, reduced]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !res) return;
    setTossed(false);
    let land: Animation | null = null;
    if (!reduced) {
      const from = getComputedStyle(el).left;
      flight.current?.cancel();
      land = el.animate([{ left: from }, { left: IN_LAND }], { duration: 420, easing: "cubic-bezier(0.16, 1, 0.3, 1)", fill: "forwards" });
    }
    const t = res.ok ? null : setTimeout(() => setTossed(true), 1900);
    return () => {
      land?.cancel();
      if (t) clearTimeout(t);
    };
  }, [res, reduced]);

  const receipt = res?.type === "ACK" || res?.type === "SACK";
  const cls = !res ? "is-flying" : res.ok ? (res.repaired > 0 ? "is-ok is-repaired" : "is-ok") : "is-bad";
  return (
    <div ref={ref} className={`sl-parcel is-in ${cls}${p.shield ? " is-shield" : ""}${receipt ? " is-receipt" : ""}${tossed ? " is-tossed" : ""}`} style={reduced ? { left: res ? IN_LAND : "50%" } : undefined}>
      <div className="sl-parcel-box">
        {p.shield && <ShieldCheck className="sl-parcel-shield" aria-hidden strokeWidth={2} />}
        <div className="sl-parcel-words">
          {!res ? (
            <span className="sl-parcel-dots" aria-label="arriving">
              <i />
              <i />
              <i />
            </span>
          ) : (
            <span className="sl-parcel-text">{receipt ? "Receipt" : res.text ?? kindWord(res.type)}</span>
          )}
          <span className="sl-parcel-tech">{res ? res.type ?? "frame" : "incoming"}</span>
        </div>
        <Seal state={!res ? "plain" : res.ok ? "ok" : "bad"} />
        {res && !res.ok && (
          <svg className="sl-parcel-crack" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
            <path d="M58 0 L52 11 L61 19 L49 29 L55 40" />
          </svg>
        )}
      </div>
      {res && (
        <div className="sl-verdict">
          <span className="sl-verdict-plain">{res.ok ? (res.repaired > 0 ? `${res.repaired} bit${res.repaired === 1 ? "" : "s"} repaired` : "Seal intact") : "Seal broken"}</span>
          <span className="sl-verdict-tech">{res.crcRx && res.crcCalc ? `${res.crcRx} ${res.ok ? "=" : "≠"} ${res.crcCalc}` : res.ok ? "CRC OK" : "CRC failed"}</span>
        </div>
      )}
    </div>
  );
}

function Say({ c }: { c: Caption }) {
  const Icon = ICONS[c.icon];
  return (
    <div className={`sl-say is-${c.tone}`} role="status" aria-live="polite">
      <span className="sl-say-mark" aria-hidden="true">
        <Icon strokeWidth={1.6} />
      </span>
      <p className="sl-say-plain">{c.plain}</p>
      <p className="sl-say-tech">{c.tech}</p>
    </div>
  );
}

const heardAgo = (t?: number) => {
  if (!t) return null;
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 5 ? "heard just now" : s < 90 ? `heard ${Math.round(s)} s ago` : `heard ${Math.round(s / 60)} min ago`;
};

export function Stage() {
  const status = useStore((s) => s.status);
  const scene = useStore((s) => s.scene);
  const conn = useStore((s) => s.conn);
  const reduced = useReducedMotion();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodeRef = useRef<HTMLDivElement>(null);
  const opts = useRef<CanvasOpts>({ freq0: 2500, freq1: 3500, shield: false, reduced, nodeR: 40, fontPx: 11, meX: 0.1, laneY: 0.3 });
  const [feed, setFeed] = useState(true);
  const [, tick] = useState(0);

  const freq0 = status?.freq0 ?? 2500;
  const freq1 = status?.freq1 ?? 3500;
  opts.current.freq0 = freq0;
  opts.current.freq1 = freq1;
  opts.current.shield = status?.shield === true;
  opts.current.noiseKind = status?.noise?.on ? status.noise.kind : undefined;
  opts.current.reduced = reduced;

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const measure = () => {
      const n = nodeRef.current;
      const stage = c.parentElement;
      if (n && stage && stage.clientWidth > 0) {
        opts.current.nodeR = n.offsetWidth / 2;
        opts.current.fontPx = Math.max(10, Math.min(12.5, n.offsetWidth * 0.13));
        opts.current.meX = (n.offsetLeft + n.offsetWidth / 2) / stage.clientWidth;
        opts.current.laneY = (n.offsetTop + n.offsetHeight / 2) / stage.clientHeight;
      }
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(c);
    const stop = startAirCanvas(c, () => opts.current);
    // once a second: is the sound feed alive, and how long since the peer was heard
    const id = setInterval(() => {
      setFeed(performance.now() - air.at < 2500);
      tick((v) => (v + 1) % 1000);
    }, 1000);
    return () => {
      stop();
      clearInterval(id);
      ro.disconnect();
    };
  }, []);

  const me = status?.node_id;
  const others = Object.keys(status?.nodes ?? {}).map(Number).filter((n) => n !== me);
  const peer = status?.peer ?? others[0] ?? null;
  const also = others.filter((n) => n !== peer);
  const heard = peer !== null ? heardAgo(status?.nodes?.[String(peer)]?.last_heard) : null;
  const mode = status?.mode;
  const fLo = air.latest?.f_lo ?? 500;
  const fHi = air.latest?.f_hi ?? 6000;
  const at = (hz: number) => `${(GEOM.eqX0 + ((hz - fLo) / (fHi - fLo)) * (GEOM.eqX1 - GEOM.eqX0)) * 100}%`;
  const simulated = mode === "simulated";

  return (
    <section className={`sl-stage${status?.shield ? " is-shielded" : ""}${status?.noise?.on ? " is-noisy" : ""}${conn !== "live" ? " is-down" : ""}`} aria-label="The air between the laptops">
      <canvas ref={canvasRef} className="sl-stage-canvas" aria-hidden="true" />
      {scene.flash && <span key={scene.flash.key} className={`sl-flash is-${scene.flash.kind}`} aria-hidden="true" />}

      <header className="sl-stage-head">
        <span className="sl-eyebrow">The air</span>
        {simulated && <span className="sl-tag is-warn">Simulated air · no real sound</span>}
        {mode === "pair" && <span className="sl-tag">Pair · both nodes share this laptop's speaker and mic</span>}
        {mode === "acoustic" && <span className="sl-tag">Real sound</span>}
        {status?.noise?.on && (
          <span className="sl-tag is-static">
            <AudioLines aria-hidden strokeWidth={2} /> Generated {status.noise.kind ?? "noise"} on
          </span>
        )}
        {status?.shield && (
          <span className="sl-tag is-good">
            <ShieldCheck aria-hidden strokeWidth={2} /> Noise Shield on
          </span>
        )}
      </header>

      <div className="sl-node is-me" ref={nodeRef}>
        <span className="sl-node-disc">
          <Laptop aria-hidden strokeWidth={1.3} />
        </span>
        <span className="sl-node-name">You · node {me ?? "–"}</span>
        <span className="sl-node-sub" title={status?.channel_label}>{simulated ? "software air" : mode === "pair" ? "shared speaker + mic" : "speaker + mic"}</span>
      </div>
      <div className={`sl-node is-peer${peer === null ? " is-unknown" : ""}`}>
        <span className="sl-node-disc">
          <Laptop aria-hidden strokeWidth={1.3} />
          {scene.wait && (
            <svg key={scene.wait.key} className="sl-wait" viewBox="0 0 100 100" aria-hidden="true">
              <circle cx="50" cy="50" r="47" pathLength="100" style={{ animationDuration: `${scene.wait.rto}s` }} />
            </svg>
          )}
        </span>
        <span className="sl-node-name">{peer === null ? "Not heard yet" : `Node ${peer}`}</span>
        <span className="sl-node-sub">{scene.wait ? "receipt due…" : peer === null ? "discover in Tools" : heard ?? "the other laptop"}</span>
        {also.length > 0 && <span className="sl-node-sub">also heard: node {also.join(", ")}</span>}
      </div>
      <span className="sl-lane-key is-1" aria-hidden="true">1</span>
      <span className="sl-lane-key is-0" aria-hidden="true">0</span>

      {scene.out && <Out key={scene.out.key} p={scene.out} reduced={reduced} />}
      {scene.inc && <In key={scene.inc.key} p={scene.inc} reduced={reduced} />}

      <Say key={scene.caption.key} c={scene.caption} />

      <div className="sl-eq-head">
        <span className="sl-eyebrow">What the microphone hears{simulated ? " · simulated" : ""}</span>
        {!feed && <span className="sl-tag">No live sound data from the engine</span>}
      </div>
      <div className="sl-eq-key is-0" style={{ left: at(freq0) }}>
        <b><span className="sl-wide">Low beep · </span>0</b>
        <span>{freq0} Hz</span>
      </div>
      <div className="sl-eq-key is-1" style={{ left: at(freq1) }}>
        <b><span className="sl-wide">High beep · </span>1</b>
        <span>{freq1} Hz</span>
      </div>
      <span className="sl-eq-end is-lo">{fLo} Hz</span>
      <span className="sl-eq-end is-hi">{fHi >= 1000 ? `${fHi / 1000} kHz` : `${fHi} Hz`}</span>
    </section>
  );
}
