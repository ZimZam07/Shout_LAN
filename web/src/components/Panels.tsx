import { useEffect, useRef, useState, type FormEvent } from "react";
import { AlertCircle, ArrowUp, AudioLines, Check, CheckCheck, Clock, PackageCheck, RotateCw, ShieldCheck, ShieldOff, Sparkle, Trash2, type LucideIcon } from "lucide-react";
import { command, tryWord, useStore, type Msg } from "../lib/store";

const MAX_CHARS = 60;

// ── the conversation: a chat, because everyone already reads ticks ──────────
function Ticks({ m }: { m: Msg }) {
  if (m.dir === "in") return <span className="sl-msg-meta">from node {m.peer}</span>;
  const part = m.frames && m.frames > 1 ? ` · parcel ${Math.min(m.done + 1, m.frames)} of ${m.frames}` : "";
  switch (m.status) {
    case "queued":
      return (
        <span className="sl-msg-meta">
          <Clock aria-hidden strokeWidth={2} /> Queued
        </span>
      );
    case "sending":
      return (
        <span className="sl-msg-meta is-sending">
          <Check aria-hidden strokeWidth={2.4} /> Sending{part}
          {m.tries > 0 && <em> · {tryWord(m.tries)}</em>}
        </span>
      );
    case "delivered":
      return (
        <span className="sl-msg-meta is-good">
          <CheckCheck aria-hidden strokeWidth={2.4} /> Delivered{m.seconds !== undefined ? ` in ${m.seconds.toFixed(1)} s` : ""}
          {m.tries > 0 && <em>took {m.tries + 1} tries</em>}
        </span>
      );
    case "failed":
      return (
        <span className="sl-msg-meta is-bad">
          <AlertCircle aria-hidden strokeWidth={2.2} /> Not delivered
        </span>
      );
    default:
      return null;
  }
}

export function Chat() {
  const messages = useStore((s) => s.messages);
  const status = useStore((s) => s.status);
  const conn = useStore((s) => s.conn);
  const [text, setText] = useState("");
  const [dstText, setDstText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const me = status?.node_id;
  const known = Object.keys(status?.nodes ?? {}).map(Number).filter((n) => n !== me);
  const fallback = status?.peer ?? known[0] ?? null;
  const dst = dstText.trim() ? Number(dstText) : fallback;
  const dstOk = dst !== null && Number.isInteger(dst) && dst >= 1 && dst <= 254 && dst !== me;
  const bytes = new TextEncoder().encode(text).length;

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // how long this text will beep, from the engine's own bit time (11 bytes of frame around each 24 of text)
  let seconds: number | null = null;
  if (status?.bit_time && bytes > 0) {
    const frames = Math.ceil(bytes / 24);
    const bits = (bytes + frames * 11) * 8;
    const rate = status.shield && status.shield_info?.rate ? status.shield_info.rate : 1;
    seconds = (bits / rate) * status.bit_time;
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !dstOk || dst === null) return;
    void command({ cmd: "send", dst, text: t }).then((ok) => ok && setText(""));
  };

  return (
    <section className="sl-card sl-chat" aria-label="Conversation">
      <header className="sl-card-head">
        <span className="sl-eyebrow">Conversation</span>
        <span className="sl-card-aside">ticks appear when the receipt is heard</span>
      </header>
      <div className="sl-chat-list" ref={listRef} role="log" aria-live="polite">
        {messages.length === 0 && (
          <p className="sl-empty">
            Nothing sent yet.
            <br />
            Type a message; it leaves this laptop as beeps.
          </p>
        )}
        {messages.map((m) => (
          <div key={m.key} className={`sl-msg is-${m.dir} is-${m.status}`}>
            <p className="sl-msg-text">{m.text}</p>
            <Ticks m={m} />
          </div>
        ))}
      </div>
      <form className="sl-compose" onSubmit={submit}>
        <div className="sl-compose-row">
          <input
            className="sl-input"
            value={text}
            maxLength={MAX_CHARS}
            onChange={(e) => setText(e.target.value)}
            placeholder="Say something out loud…"
            aria-label="Message"
            autoComplete="off"
            spellCheck={false}
          />
          <button className="sl-send" type="submit" disabled={!text.trim() || !dstOk || conn !== "live"} aria-label={`Send to node ${dst ?? "?"}`}>
            <ArrowUp aria-hidden strokeWidth={2.4} />
          </button>
        </div>
        <div className="sl-compose-meta">
          <label className="sl-to">
            to node
            <input
              value={dstText}
              onChange={(e) => setDstText(e.target.value.replace(/\D/g, "").slice(0, 3))}
              placeholder={fallback !== null ? String(fallback) : "?"}
              inputMode="numeric"
              aria-label="Destination node"
              aria-invalid={!dstOk}
            />
          </label>
          <span>{seconds !== null ? `about ${seconds.toFixed(1)} s of beeps · ` : ""}{text.length}/{MAX_CHARS}</span>
        </div>
      </form>
    </section>
  );
}

// ── the switch both controls share ──────────────────────────────────────────
function Switch({ on, onChange, label, OnIcon, OffIcon, big, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; OnIcon: LucideIcon; OffIcon: LucideIcon; big?: boolean; disabled?: boolean }) {
  const Icon = on ? OnIcon : OffIcon;
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} className={`sl-switch${big ? " is-big" : ""}${on ? " is-on" : ""}`} onClick={() => onChange(!on)}>
      <span className="sl-switch-word">{on ? "On" : "Off"}</span>
      <span className="sl-switch-knob">
        <Icon aria-hidden strokeWidth={2} />
      </span>
    </button>
  );
}

// ── Noise Shield: the hero control, on the one paper card ───────────────────
export function ShieldCard() {
  const status = useStore((s) => s.status);
  const conn = useStore((s) => s.conn);
  const on = status?.shield === true;
  const known = status?.shield !== undefined && status.shield_available !== false;
  const si = status?.shield_info;
  const repaired = status?.stats?.counters?.bits_repaired;
  const what = [si?.code, si?.rate !== undefined ? `rate ${si.rate.toFixed(2)}` : undefined, si?.airtime_factor ? `${si.airtime_factor}× air time` : undefined].filter(Boolean).join(" · ");
  return (
    <section className={`sl-shield${on ? " is-on" : ""}`} aria-label="Noise Shield">
      <div className="sl-shield-top">
        <div>
          <span className="sl-eyebrow">Noise Shield</span>
          <h2 className="sl-shield-title">{on ? "Parcels repair themselves" : "Parcels travel unprotected"}</h2>
        </div>
        <Switch big on={on} label="Noise Shield" OnIcon={ShieldCheck} OffIcon={ShieldOff} disabled={!known || conn !== "live"} onChange={(v) => void command({ cmd: "set_shield", value: v })} />
      </div>
      <p className="sl-shield-say">
        {known
          ? on
            ? "Every message carries spare bits. Noise still hits it, but the receiver repairs the damage, with no resend."
            : "Like shouting in a loud room: one garbled word and the whole message is thrown away and said again."
          : status ? "Not available: this engine build has no Noise Shield yet." : "Waiting for the engine."}
      </p>
      <div className="sl-shield-foot">
        <div className="sl-shield-num" aria-live="polite">
          <b key={repaired}>{repaired ?? "–"}</b>
          <span>
            bits repaired
            {status?.mode === "simulated" && <i> · simulated</i>}
          </span>
        </div>
        {what && (
          <p className="sl-shield-tech">
            <span>What it really is</span>
            {what}
          </p>
        )}
      </div>
    </section>
  );
}

// ── the interference generator ──────────────────────────────────────────────
const KINDS: { id: string; name: string; say: string; Icon: LucideIcon }[] = [
  { id: "clicks", name: "Clicks", say: "sharp bursts", Icon: Sparkle },
  { id: "hiss", name: "Hiss", say: "steady noise", Icon: AudioLines },
];
export function NoiseCard() {
  const status = useStore((s) => s.status);
  const conn = useStore((s) => s.conn);
  const available = status?.noise_available !== false;
  const noise = available ? status?.noise : undefined;
  const on = noise?.on === true;
  const [level, setLevel] = useState<number | null>(null);
  const shown = level ?? noise?.level ?? 0.25;
  const sim = status?.mode === "simulated";
  const kind = noise?.kind; // absent on an engine with a single kind: no choice is offered
  const busy = !noise || conn !== "live";
  const send = (next: { on?: boolean; level?: number; kind?: string }) =>
    // a level of 0 means "never set": leave it out and the engine uses its own default
    void command({ cmd: "set_noise", on: next.on ?? on, ...((next.level ?? shown) > 0 ? { level: next.level ?? shown } : {}), ...((next.kind ?? kind) ? { kind: next.kind ?? kind } : {}) });
  const commit = (v: number) => {
    setLevel(null);
    send({ level: v });
  };
  const title = kind === "clicks" ? (on ? "Clicks in the room" : "Fill the room with clicks") : kind === "hiss" ? (on ? "Hiss in the room" : "Fill the room with hiss") : on ? "The room is loud" : "Make the room loud";
  return (
    <section className={`sl-card sl-noise${on ? " is-on" : ""}`} aria-label="Generated noise">
      <div className="sl-noise-top">
        <div>
          <span className="sl-eyebrow">Generated noise</span>
          <h2 className="sl-noise-title">{title}</h2>
        </div>
        <Switch on={on} label="Generated noise" OnIcon={AudioLines} OffIcon={AudioLines} disabled={busy} onChange={(v) => send({ on: v })} />
      </div>
      {kind !== undefined && (
        <div className="sl-seg" role="radiogroup" aria-label="Kind of generated noise">
          {KINDS.map(({ id, name, say, Icon }) => (
            <button key={id} type="button" role="radio" aria-checked={kind === id} className={kind === id ? "is-on" : ""} disabled={busy} onClick={() => kind !== id && send({ kind: id })}>
              <Icon aria-hidden strokeWidth={2} />
              <span>
                <b>{name}</b>
                <small>{say}</small>
              </span>
            </button>
          ))}
        </div>
      )}
      <label className="sl-slider">
        <span>Level</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={shown}
          disabled={busy}
          onChange={(e) => setLevel(Number(e.target.value))}
          onPointerUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
          onKeyUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
          style={{ ["--fill" as string]: `${shown * 100}%` }}
          aria-label="Generated noise level"
        />
        <b>{Math.round(shown * 100)}%</b>
      </label>
      <p className="sl-note">{!status ? "Waiting for the engine." : !available ? "Not available: this engine build has no interference generator yet." : sim ? "Added to the simulated air. No real sound." : "Really played by this laptop's speaker."}</p>
    </section>
  );
}

// ── the numbers, each straight from status.stats.counters ───────────────────
const STATS: { key: string; plain: string; tech: string; Icon: LucideIcon; tone?: "bad" | "good" }[] = [
  { key: "frames_tx", plain: "Parcels sent", tech: "frames sent", Icon: ArrowUp },
  { key: "crc_failures", plain: "Thrown away", tech: "rejected by CRC", Icon: Trash2, tone: "bad" },
  { key: "retransmissions", plain: "Sent again", tech: "retransmissions", Icon: RotateCw },
  { key: "messages_delivered", plain: "Delivered", tech: "messages acked", Icon: PackageCheck, tone: "good" },
];

export function Stats() {
  const counters = useStore((s) => s.status?.stats?.counters);
  const sim = useStore((s) => s.status?.mode === "simulated");
  return (
    <section className="sl-stats" aria-label="Counters">
      <span className="sl-eyebrow">This run{sim ? " · simulated" : ""}</span>
      {STATS.map(({ key, plain, tech, Icon, tone }) => {
        const v = counters?.[key];
        return (
          <div className={`sl-stat${tone ? ` is-${tone}` : ""}`} key={key}>
            <span className="sl-stat-mark" aria-hidden="true">
              <Icon strokeWidth={1.5} />
            </span>
            <dl>
              <dt>{plain}</dt>
              <dd>{tech}</dd>
            </dl>
            <b key={v}>{v ?? "–"}</b>
          </div>
        );
      })}
    </section>
  );
}
