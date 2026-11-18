import { air } from "../lib/store";
import { GOOD, INK, STATIC, TONE0, TONE1, rgba } from "../lib/palette";
import type { SpectrumEvent } from "../lib/types";

/* The stage canvas: everything on it is drawn from `spectrum` events.
 *
 *   lanes + beads   each event, a tone that stands above its neighbours drops a
 *                   bead on its lane (high beep = 1 on top, low beep = 0 below).
 *                   Beads leave this node while we transmit, and come from the
 *                   other side while we only hear them.
 *   static          specks in the air, as many as the noise in the tone band;
 *                   when the generator plays clicks, each burst heard is a flash
 *   ripples         rings around whichever node is sounding
 *   equaliser       the 48 bins, tone bins in the tone colours, the noise level
 *                   as a dashed line
 *
 * Nothing here allocates per frame beyond a bounded bead list, and nothing
 * goes through React, so it runs for as long as the demo does. */

export const GEOM = { eqX0: 0.055, eqX1: 0.945, eqY0: 0.74, eqY1: 0.905 };

export interface CanvasOpts {
  freq0: number;
  freq1: number;
  shield: boolean;
  /** "clicks" draws each heard burst as a flash; anything else is steady static */
  noiseKind?: string;
  reduced: boolean;
  nodeR: number;
  fontPx: number;
  /** where the stage CSS put this node, as fractions of the stage */
  meX: number;
  laneY: number;
}

const BEAD_MS = 1500;
const MAX_BEADS = 120;
const MAX_SPECKS = 170;
const STALE_MS = 1200;

interface Bead { lane: 0 | 1; t0: number; dir: 1 | -1; s: number }
interface Ripple { at: "me" | "peer"; t0: number }
interface Flash { x: number; y: number; t0: number; size: number }

export function startAirCanvas(canvas: HTMLCanvasElement, getOpts: () => CanvasOpts): () => void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return () => {};

  let w = 0;
  let h = 0;
  const fit = () => {
    const r = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = r.width;
    h = r.height;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  const ro = new ResizeObserver(fit);
  ro.observe(canvas);
  fit();

  let bars = new Float32Array(48);
  let peaks = new Float32Array(48);
  let target = new Float32Array(48);
  const beads: Bead[] = [];
  const ripples: Ripple[] = [];
  const flashes: Flash[] = [];
  let burst = 0;
  const specks = new Float32Array(MAX_SPECKS * 4); // x, y, born, life
  let noise = 0; // 0..1, noise in the tone band
  let noiseTarget = 0;
  let glow0 = 0;
  let glow1 = 0;
  let level = 0;
  let levelTarget = 0;
  let shieldMix = 0;
  let lastRipple = 0;
  let fLo = 500;
  let fHi = 6000;

  const norm = (db: number) => Math.max(0, Math.min(1, (db + 90) / 82));

  const onSample = (s: SpectrumEvent) => {
    const o = getOpts();
    const n = s.bins.length;
    if (bars.length !== n) {
      bars = new Float32Array(n);
      peaks = new Float32Array(n);
      target = new Float32Array(n);
    }
    fLo = s.f_lo;
    fHi = s.f_hi;
    const step = (fHi - fLo) / n;
    const binOf = (hz: number) => Math.max(0, Math.min(n - 1, Math.floor((hz - fLo) / step)));
    for (let i = 0; i < n; i++) target[i] = norm(s.bins[i]);
    const b0 = binOf(o.freq0);
    const b1 = binOf(o.freq1);
    // a tone counts when it stands clear of the bins beside it, whatever the room is doing
    const around = (b: number) => {
      let sum = 0;
      let k = 0;
      for (const d of [-3, -2, 2, 3]) {
        const i = b + d;
        if (i >= 0 && i < n && i !== b0 && i !== b1) {
          sum += s.bins[i];
          k++;
        }
      }
      return k ? sum / k : -90;
    };
    const s0 = Math.max(0, Math.min(1, (s.bins[b0] - around(b0) - 9) / 20));
    const s1 = Math.max(0, Math.min(1, (s.bins[b1] - around(b1) - 9) / 20));
    // noise: the average of the band the tones live in, tones excluded
    let sum = 0;
    let k = 0;
    for (let i = binOf(o.freq0 - 700); i <= binOf(o.freq1 + 700); i++) {
      if (Math.abs(i - b0) > 1 && Math.abs(i - b1) > 1) {
        sum += s.bins[i];
        k++;
      }
    }
    const heard = norm(k ? sum / k : s.noise_db);
    // a click is a sample that jumps well clear of the running noise level
    if (o.noiseKind === "clicks" && heard - noise > 0.1) burst = Math.min(4, burst + 1 + Math.round((heard - noise) * 8));
    noiseTarget = o.noiseKind === "clicks" ? Math.min(heard, noiseTarget + 0.02) : heard;
    levelTarget = Math.max(0, Math.min(1, s.level * 2.2));
    const now = performance.now();
    const dir = s.transmitting ? 1 : -1;
    if (s0 > 0.18) beads.push({ lane: 0, t0: now, dir, s: s0 });
    if (s1 > 0.18) beads.push({ lane: 1, t0: now, dir, s: s1 });
    if (beads.length > MAX_BEADS) beads.splice(0, beads.length - MAX_BEADS);
    glow0 = Math.max(glow0, s0);
    glow1 = Math.max(glow1, s1);
    if ((s.transmitting || s0 > 0.3 || s1 > 0.3) && now - lastRipple > 420) {
      lastRipple = now;
      ripples.push({ at: s.transmitting ? "me" : "peer", t0: now });
      if (ripples.length > 8) ripples.shift();
    }
  };
  air.listeners.add(onSample);
  if (air.latest) onSample(air.latest);

  let raf = 0;
  let prev = performance.now();
  const frame = (now: number) => {
    raf = requestAnimationFrame(frame);
    if (w < 2 || h < 2) return;
    const o = getOpts();
    const dt = Math.min(64, now - prev);
    prev = now;
    const stale = now - air.at > STALE_MS;
    const k = (rate: number) => (o.reduced ? 1 : 1 - Math.exp(-dt * rate));

    ctx.clearRect(0, 0, w, h);
    const R = o.nodeR || 40;
    const xMe = w * o.meX;
    const xPeer = w * (1 - o.meX);
    const yMid = h * o.laneY;
    const x0 = xMe + R + 26;
    const x1 = xPeer - R - 26;
    const gap = R * 0.36;
    const laneY = [yMid + gap, yMid - gap]; // lane 0 (low beep) below, lane 1 (high beep) above

    noise += ((stale ? 0 : noiseTarget) - noise) * k(0.006);
    level += ((stale ? 0 : levelTarget) - level) * k(0.012);
    glow0 *= o.reduced ? 0.9 : Math.exp(-dt * 0.004);
    glow1 *= o.reduced ? 0.9 : Math.exp(-dt * 0.004);
    shieldMix += ((o.shield ? 1 : 0) - shieldMix) * k(0.005);

    // ── static: the noise in the room ──
    const want = stale ? 0 : Math.round(Math.max(0, noise - 0.13) * 1.75 * MAX_SPECKS);
    const count = Math.min(MAX_SPECKS, want);
    const top = h * 0.1;
    const bottom = h * 0.5;
    ctx.fillStyle = rgba(STATIC, 1);
    for (let i = 0; i < count; i++) {
      const j = i * 4;
      if (now > specks[j + 2] + specks[j + 3]) {
        specks[j] = x0 + Math.random() * (x1 - x0);
        specks[j + 1] = top + Math.random() * (bottom - top);
        specks[j + 2] = o.reduced ? now + 1e9 : now - Math.random() * 120;
        specks[j + 3] = 260 + Math.random() * 700;
        if (o.reduced) specks[j + 3] = 1; // fixed field: placed once, never blinking
      }
      const age = (now - specks[j + 2]) / specks[j + 3];
      const a = o.reduced ? 0.45 : Math.sin(Math.PI * Math.max(0, Math.min(1, age))) * 0.7;
      const size = 1.2 + ((i * 7) % 5) * 0.35;
      ctx.globalAlpha = a;
      ctx.fillRect(specks[j], specks[j + 1], size, size);
    }
    ctx.globalAlpha = 1;

    // ── clicks: one flash per burst heard ──
    while (burst > 0 && !o.reduced) {
      burst--;
      flashes.push({ x: x0 + Math.random() * (x1 - x0), y: top + Math.random() * (bottom - top), t0: now - Math.random() * 60, size: 0.7 + Math.random() * 0.7 });
      if (flashes.length > 14) flashes.shift();
    }
    burst = 0;
    for (let i = flashes.length - 1; i >= 0; i--) {
      const f = flashes[i];
      const p = (now - f.t0) / 280;
      if (p >= 1) {
        flashes.splice(i, 1);
        continue;
      }
      const reach = R * f.size * (0.12 + 0.34 * Math.max(0, p));
      ctx.strokeStyle = rgba(STATIC, 0.95 * (1 - p));
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      for (let r = 0; r < 4; r++) {
        const ang = (Math.PI / 4) * r + f.size;
        ctx.moveTo(f.x - Math.cos(ang) * reach, f.y - Math.sin(ang) * reach);
        ctx.lineTo(f.x + Math.cos(ang) * reach, f.y + Math.sin(ang) * reach);
      }
      ctx.stroke();
    }

    // ── lanes ──
    for (const lane of [0, 1] as const) {
      const c = lane === 0 ? TONE0 : TONE1;
      const g = lane === 0 ? glow0 : glow1;
      ctx.lineWidth = 1;
      ctx.strokeStyle = rgba(INK, 0.1);
      ctx.beginPath();
      ctx.moveTo(x0, laneY[lane]);
      ctx.lineTo(x1, laneY[lane]);
      ctx.stroke();
      if (g > 0.02) {
        ctx.strokeStyle = rgba(c, g * 0.5);
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }
    // the shield: a calm sleeve around the lanes while it is on
    if (shieldMix > 0.01) {
      ctx.strokeStyle = rgba(GOOD, 0.34 * shieldMix);
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 6]);
      const pad = gap * 1.1;
      ctx.beginPath();
      ctx.roundRect(x0 - 10, yMid - gap - pad, (x1 - x0 + 20) * shieldMix, 2 * (gap + pad), gap + pad);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // ── beads: the sound itself ──
    for (let i = beads.length - 1; i >= 0; i--) {
      const b = beads[i];
      const p = (now - b.t0) / BEAD_MS;
      if (p >= 1) {
        beads.splice(i, 1);
        continue;
      }
      if (o.reduced) continue;
      const x = b.dir > 0 ? x0 + p * (x1 - x0) : x1 - p * (x1 - x0);
      const a = b.s * Math.min(1, p * 9, (1 - p) * 9);
      const r = R * (0.05 + 0.065 * b.s);
      const c = b.lane === 0 ? TONE0 : TONE1;
      ctx.fillStyle = rgba(c, a * 0.16);
      ctx.beginPath();
      ctx.arc(x, laneY[b.lane], r * 2.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = rgba(c, a);
      ctx.beginPath();
      ctx.arc(x, laneY[b.lane], r, 0, Math.PI * 2);
      ctx.fill();
    }

    // ── ripples: who is sounding ──
    for (let i = ripples.length - 1; i >= 0; i--) {
      const p = (now - ripples[i].t0) / 1300;
      if (p >= 1) {
        ripples.splice(i, 1);
        continue;
      }
      if (o.reduced) continue;
      ctx.strokeStyle = rgba(INK, 0.26 * (1 - p));
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(ripples[i].at === "me" ? xMe : xPeer, yMid, R + 8 + p * R * 0.9, 0, Math.PI * 2);
      ctx.stroke();
    }
    // the microphone level, as an arc around this node
    if (level > 0.01) {
      ctx.strokeStyle = rgba(INK, 0.8);
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.arc(xMe, yMid, R + 6, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, level));
      ctx.stroke();
      ctx.lineCap = "butt";
    }

    // ── the equaliser: what the microphone hears ──
    const n = bars.length;
    const ex0 = w * GEOM.eqX0;
    const ex1 = w * GEOM.eqX1;
    const ey0 = h * GEOM.eqY0;
    const ey1 = h * GEOM.eqY1;
    const eh = ey1 - ey0;
    const bw = (ex1 - ex0) / n;
    const step = (fHi - fLo) / n;
    const binOf = (hz: number) => Math.max(0, Math.min(n - 1, Math.floor((hz - fLo) / step)));
    const b0 = binOf(o.freq0);
    const b1 = binOf(o.freq1);
    for (let i = 0; i < n; i++) {
      const t = stale ? 0 : target[i];
      bars[i] += (t - bars[i]) * k(t > bars[i] ? 0.03 : 0.008);
      peaks[i] = Math.max(bars[i], peaks[i] - dt * 0.0006);
      const outside = 1;
      const bh = Math.max(1.5, bars[i] * eh);
      const x = ex0 + i * bw + bw * 0.22;
      const tone = i === b0 ? TONE0 : i === b1 ? TONE1 : null;
      ctx.fillStyle = tone ? rgba(tone, 0.95) : rgba(INK, (0.16 + bars[i] * 0.2) * outside);
      ctx.beginPath();
      ctx.roundRect(x, ey1 - bh, bw * 0.56, bh, [Math.min(3, bw * 0.28), Math.min(3, bw * 0.28), 0, 0]);
      ctx.fill();
      if (!o.reduced && peaks[i] - bars[i] > 0.015) {
        ctx.fillStyle = tone ? rgba(tone, 0.6) : rgba(INK, 0.3 * outside);
        ctx.fillRect(x, ey1 - peaks[i] * eh - 1, bw * 0.56, 1.5);
      }
    }
    ctx.fillStyle = rgba(INK, 0.14);
    ctx.fillRect(ex0, ey1, ex1 - ex0, 1);

    ctx.font = `500 ${o.fontPx}px "Lexend Variable", sans-serif`;
    ctx.textBaseline = "middle";
    if ("letterSpacing" in ctx) (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${o.fontPx * 0.14}px`;
    if (!stale) {
      const ny = ey1 - noise * eh;
      ctx.strokeStyle = rgba(STATIC, 0.6);
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(ex0, ny);
      ctx.lineTo(ex1, ny);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = rgba(STATIC, 0.92);
      ctx.textAlign = "right";
      ctx.fillText("NOISE LEVEL", ex1, ny - o.fontPx * 0.95);
    }
  };
  raf = requestAnimationFrame(frame);

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    air.listeners.delete(onSample);
  };
}
