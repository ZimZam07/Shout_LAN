import { useEffect, useRef } from "react";
import { ArrowDownLeft, ArrowUpRight, Check, Equal, EqualNot, RotateCw, ShieldCheck, X } from "lucide-react";
import { kindWord, payloadText, select, tryWord, useStore, type FrameRec } from "../lib/store";
import { useEscape } from "./hooks";

/* Every frame, in order: the parcel track under the stage. One chip per frame
 * says which way it went, what it was and how it fared, in a word and an icon.
 * Choosing one opens the parcel: its layers, its seal, its bytes, its bits. */

function verdict(f: FrameRec): { cls: string; word: string; Icon: typeof Check } {
  if (f.dir === "tx") return f.retry > 0 ? { cls: "again", word: tryWord(f.retry), Icon: RotateCw } : { cls: "sent", word: "Sent", Icon: ArrowUpRight };
  if (!f.ok) return { cls: "bad", word: "Damaged", Icon: X };
  if (f.bitsRepaired > 0) return { cls: "mended", word: `Repaired ${f.bitsRepaired}`, Icon: ShieldCheck };
  return { cls: "ok", word: "Intact", Icon: Check };
}

export function FrameTrack() {
  const frames = useStore((s) => s.frames);
  const selected = useStore((s) => s.selected);
  const ref = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollLeft = el.scrollWidth;
  }, [frames]);

  return (
    <section className="sl-card sl-track" aria-label="Every frame, in order">
      <header className="sl-card-head">
        <span className="sl-eyebrow">Every parcel, in order</span>
        <span className="sl-card-aside">one per frame · choose one to open it</span>
      </header>
      <div
        className="sl-track-row"
        ref={ref}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollWidth - el.scrollLeft - el.clientWidth < 40;
        }}
      >
        {frames.length === 0 && <p className="sl-empty is-inline">No parcels yet. Each message you send appears here, with its receipt.</p>}
        {frames.map((f) => {
          const v = verdict(f);
          const Dir = f.dir === "tx" ? ArrowUpRight : ArrowDownLeft;
          return (
            <button key={f.key} type="button" className={`sl-chip is-${v.cls}${selected === f.key ? " is-open" : ""}`} aria-pressed={selected === f.key} onClick={() => select(selected === f.key ? null : f.key)}>
              <span className="sl-chip-kind">
                <Dir aria-hidden strokeWidth={2} />
                {kindWord(f.info?.type)}
              </span>
              <span className="sl-chip-tech">
                {f.dir === "tx" ? "out" : "in"} · {f.info?.type ?? "?"}
                {f.info?.seq !== undefined ? ` ${f.info.seq}` : ""}
              </span>
              <span className="sl-chip-state">
                <v.Icon aria-hidden strokeWidth={2.4} />
                {v.word}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

// ── the detail view ─────────────────────────────────────────────────────────
const parseHex = (raw?: string): number[] | null => {
  if (!raw) return null;
  const bytes = raw.trim().split(/\s+/).map((h) => parseInt(h, 16));
  return bytes.length && !bytes.some(Number.isNaN) ? bytes : null;
};

type Region = "sync" | "link" | "net" | "app" | "crc";
/** shout_lan/frame.py: SYNC, type|flags, src, dst, seq, len, origin, final, ttl, payload, CRC-16. */
function regionOf(i: number, n: number): Region {
  if (i === 0) return "sync";
  if (i <= 5) return "link";
  if (i <= 8) return "net";
  if (i >= n - 2) return "crc";
  return "app";
}

function Bits({ f, bytes }: { f: FrameRec; bytes: number[] | null }) {
  const conf = f.bitConf;
  if (!conf || conf.length === 0) return <p className="sl-note">This engine build did not report per-bit confidence for this frame.</p>;
  const n = conf.length;
  const values = bytes && bytes.length * 8 === n ? bytes : null;
  const repaired = new Set(f.repairedAt ?? []);
  const PER = 64;
  const rows = Math.ceil(n / PER);
  const cell = 10;
  const rowH = 34;
  const weak = conf.filter((c) => c < 0.6).length;
  return (
    <>
      <svg className="sl-bits" viewBox={`0 0 ${PER * cell + 16} ${rows * rowH}`} role="img" aria-label={`${n} bits; ${weak} weak; ${repaired.size} repaired`}>
        {Array.from({ length: rows }, (_, r) => (
          <g key={r} transform={`translate(8 ${r * rowH})`}>
            <line className="sl-bits-lane" x1="0" x2={PER * cell} y1="8" y2="8" />
            <line className="sl-bits-lane" x1="0" x2={PER * cell} y1="22" y2="22" />
            {Array.from({ length: Math.min(PER, n - r * PER) }, (_, c) => {
              const i = r * PER + c;
              const bit = values ? (values[i >> 3] >> (7 - (i & 7))) & 1 : null;
              const y = bit === null ? 15 : bit ? 8 : 22;
              const x = c * cell + cell / 2;
              const region = values ? regionOf(i >> 3, values.length) : "app";
              const isRepaired = repaired.has(i);
              const isWeak = conf[i] < 0.6;
              return (
                <g key={i}>
                  {isRepaired && <circle className="sl-bit-ring" cx={x} cy={y} r="6.2" />}
                  <circle
                    className={`sl-bit is-${bit === null ? "x" : bit}${isRepaired ? " is-repaired" : isWeak ? " is-weak" : ""} in-${region}`}
                    cx={x}
                    cy={y}
                    r={isWeak && !isRepaired ? 2.8 : 3.4}
                    opacity={isRepaired || isWeak ? 1 : 0.3 + 0.7 * Math.max(0, Math.min(1, (conf[i] - 0.5) * 2))}
                  />
                </g>
              );
            })}
            {Array.from({ length: Math.ceil(Math.min(PER, n - r * PER) / 8) + 1 }, (_, b) => (
              <line key={b} className="sl-bits-tick" x1={b * 8 * cell} x2={b * 8 * cell} y1="27" y2="31" />
            ))}
          </g>
        ))}
      </svg>
      <ul className="sl-legend">
        <li><i className="sl-key is-1" /> high beep = 1</li>
        <li><i className="sl-key is-0" /> low beep = 0</li>
        <li><i className="sl-key is-weak" /> weak bit, barely heard ({weak})</li>
        <li><i className="sl-key is-repaired" /> repaired by the shield ({repaired.size})</li>
      </ul>
    </>
  );
}

const hx = (v?: number) => (v === undefined ? "–" : String(v));

export function FrameDetail() {
  const selected = useStore((s) => s.selected);
  const frames = useStore((s) => s.frames);
  const sim = useStore((s) => s.status?.mode === "simulated");
  const f = selected === null ? undefined : frames.find((x) => x.key === selected);
  const close = () => select(null);
  useEscape(!!f, close);
  if (!f) return null;

  const info = f.info;
  const bytes = parseHex(f.raw);
  const v = verdict(f);
  const title =
    f.dir === "tx"
      ? f.retry > 0
        ? `Sent again, ${tryWord(f.retry)}`
        : `Sent: ${kindWord(info?.type).toLowerCase()}`
      : !f.ok
        ? "Arrived damaged, thrown away"
        : f.bitsRepaired > 0
          ? "Arrived damaged, repaired by the shield"
          : `Arrived intact: ${kindWord(info?.type).toLowerCase()}`;
  const tech = [info?.type ? `${info.type} frame` : "undecoded frame", info?.seq !== undefined ? `seq ${info.seq}` : null, info?.src !== undefined ? `node ${info.src} → node ${info.dst === 255 ? "all" : info.dst}` : null, f.retry > 0 ? `retransmission ${f.retry}` : null, f.shield ? "shielded format" : f.shield === false ? "plain format" : null].filter(Boolean).join(" · ");
  const text = payloadText(info);
  const hasCrc = f.crcRx !== undefined && f.crcCalc !== undefined;
  const sentCrc = f.dir === "tx" && bytes && bytes.length >= 11 ? "0x" + ((bytes[bytes.length - 2] << 8) | bytes[bytes.length - 1]).toString(16).toUpperCase().padStart(4, "0") : null;
  const facts: [string, string][] = [];
  if (f.airtime !== undefined) facts.push(["on air", `${f.airtime.toFixed(2)} s`]);
  if (f.bitsOnAir !== undefined) facts.push(["bits on air", String(f.bitsOnAir)]);
  if (bytes) facts.push(["frame", `${bytes.length} bytes`]);
  if (f.snr !== undefined) facts.push(["signal vs noise", `${f.snr.toFixed(1)} dB`]);
  if (f.score !== undefined) facts.push(["chirp match", f.score.toFixed(2)]);
  facts.push(["time", new Date(f.time * 1000).toLocaleTimeString([], { hour12: false })]);

  return (
    <aside className="sl-drawer" aria-label="Frame detail">
      <header className={`sl-drawer-head is-${v.cls}`}>
        <span className="sl-drawer-mark" aria-hidden="true">
          <v.Icon strokeWidth={2} />
        </span>
        <div>
          <h2>{title}</h2>
          <p>{tech}</p>
        </div>
        <button type="button" className="sl-x" onClick={close} aria-label="Close">
          <X aria-hidden strokeWidth={1.8} />
        </button>
      </header>

      <div className="sl-drawer-body">
        {/* the seal */}
        <section className="sl-sec">
          <h3>
            The seal <small>CRC-16 checksum</small>
          </h3>
          {hasCrc ? (
            <div className={`sl-crc ${f.ok ? "is-ok" : "is-bad"}`}>
              <div>
                <span>Seal that arrived</span>
                <b>{f.crcRx}</b>
                <small>received CRC</small>
              </div>
              <span className="sl-crc-op" aria-label={f.ok ? "equals" : "does not equal"}>
                {f.ok ? <Equal aria-hidden strokeWidth={2.2} /> : <EqualNot aria-hidden strokeWidth={2.2} />}
              </span>
              <div>
                <span>Seal we worked out</span>
                <b>{f.crcCalc}</b>
                <small>calculated CRC</small>
              </div>
              <p>{f.ok ? "They match: nothing changed on the way." : "They differ: something changed on the way, so the parcel is thrown away. No receipt is sent."}</p>
            </div>
          ) : f.dir === "tx" ? (
            <p className="sl-note">Stamped on the way out{sentCrc ? <>: <b className="sl-mono">{sentCrc}</b></> : null}. The receiver works the seal out again and compares.</p>
          ) : (
            <p className="sl-note">{f.error ?? (f.ok ? "The CRC passed; this engine build did not report the two values." : "The CRC failed; this engine build did not report the two values.")}</p>
          )}
        </section>

        {/* the layers: a box inside a box inside a box */}
        <section className="sl-sec">
          <h3>
            What is inside <small>layers</small>
          </h3>
          {info ? (
            <div className="sl-layers">
              <div className="sl-layer in-link">
                <span className="sl-layer-name">Hop label <small>data-link header</small></span>
                <dl>
                  <div><dt>from</dt><dd>{hx(info.src)}</dd></div>
                  <div><dt>to</dt><dd>{info.dst === 255 ? "all" : hx(info.dst)}</dd></div>
                  <div><dt>seq</dt><dd>{hx(info.seq)}</dd></div>
                  <div><dt>len</dt><dd>{hx(info.len)}</dd></div>
                  <div><dt>type</dt><dd>{info.type ?? "–"}</dd></div>
                  {info.flags && info.flags.length > 0 && <div><dt>flags</dt><dd>{info.flags.join(" ")}</dd></div>}
                </dl>
                <div className="sl-layer in-net">
                  <span className="sl-layer-name">Route label <small>network header</small></span>
                  <dl>
                    <div><dt>origin</dt><dd>{hx(info.origin)}</dd></div>
                    <div><dt>final</dt><dd>{info.final === 255 ? "all" : hx(info.final)}</dd></div>
                    <div><dt>TTL</dt><dd>{hx(info.ttl)}</dd></div>
                  </dl>
                  <div className="sl-layer in-app">
                    <span className="sl-layer-name">The message <small>application · {info.len ?? 0} bytes</small></span>
                    <p className="sl-layer-text">{text ? `“${text}”` : info.type === "ACK" ? "empty: a receipt carries no text" : "empty"}</p>
                  </div>
                </div>
              </div>
              {info.damaged && <p className="sl-note is-bad">Read from a damaged frame: best effort, any of these may be wrong.</p>}
            </div>
          ) : (
            <p className="sl-note">{f.error ?? "The header could not be decoded."}</p>
          )}
        </section>

        {/* raw bytes */}
        <section className="sl-sec">
          <h3>
            On the wire <small>raw bytes</small>
          </h3>
          {bytes ? (
            <>
              <p className="sl-bytes">
                {bytes.map((b, i) => (
                  <span key={i} className={`in-${bytes.length >= 11 ? regionOf(i, bytes.length) : "app"}`}>
                    {b.toString(16).toUpperCase().padStart(2, "0")}
                  </span>
                ))}
              </p>
              {bytes.length >= 11 && (
                <ul className="sl-legend">
                  <li><i className="sl-key in-sync" /> start</li>
                  <li><i className="sl-key in-link" /> hop label</li>
                  <li><i className="sl-key in-net" /> route label</li>
                  <li><i className="sl-key in-app" /> message</li>
                  <li><i className="sl-key in-crc" /> seal</li>
                </ul>
              )}
            </>
          ) : (
            <p className="sl-note">No raw bytes were reported for this frame.</p>
          )}
        </section>

        {/* bits */}
        {f.dir === "rx" && (
          <section className="sl-sec">
            <h3>
              Bit by bit <small>as the microphone heard them</small>
            </h3>
            <Bits f={f} bytes={bytes} />
          </section>
        )}

        <dl className="sl-facts">
          {facts.map(([k, val]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{val}</dd>
            </div>
          ))}
        </dl>
        {sim && <p className="sl-note">Simulated air: these are not acoustic measurements.</p>}
      </div>
    </aside>
  );
}
