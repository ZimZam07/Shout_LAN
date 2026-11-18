import { useCallback, useEffect, useState } from "react";
import { fetchStatus, LiveTransport } from "./lib/client";
import { MockTransport } from "./lib/mock";
import { connect, disconnect, select, useStore } from "./lib/store";
import { FrameDetail, FrameTrack } from "./components/Frames";
import { Landing } from "./components/Landing";
import { Chat, NoiseCard, ShieldCard, Stats } from "./components/Panels";
import { Banners, Dock, LogDrawer, Ticker } from "./components/Shell";
import { Stage } from "./components/Stage";

/* Two screens: the opening (#/) and the console (#/console, the deep link).
 * The engine is the real one unless the URL says ?mock (or ?mock=manual for
 * the mock without its autoplay story); in development an unreachable engine
 * falls back to the mock by itself. */

const route = () => (window.location.hash.startsWith("#/console") ? "console" : "landing");

async function start() {
  const q = new URLSearchParams(window.location.search);
  if (q.has("mock")) return connect(new MockTransport(q.get("mock") !== "manual"));
  if (import.meta.env.DEV) {
    try {
      await fetchStatus(1200);
    } catch {
      return connect(new MockTransport(true));
    }
  }
  connect(new LiveTransport());
}

function Backdrop() {
  return (
    <div className="sl-backdrop" aria-hidden="true">
      <div className="sl-rings">
        <i />
        <i />
        <i />
        <i />
        <i />
      </div>
      <span className="sl-ghost">SHOUT</span>
    </div>
  );
}

function Console({ onHome }: { onHome: () => void }) {
  const [log, setLog] = useState(false);
  const selected = useStore((s) => s.selected);
  const closeLog = useCallback(() => setLog(false), []);
  useEffect(() => {
    if (selected !== null) setLog(false);
  }, [selected]);
  return (
    <>
      <Dock
        onHome={onHome}
        logOpen={log}
        onLog={() => {
          select(null);
          setLog((v) => !v);
        }}
      />
      <Banners />
      <main className="sl-console">
        <div className="sl-col is-left">
          <Chat />
        </div>
        <div className="sl-col is-mid">
          <Stage />
          <FrameTrack />
          <Ticker onOpen={() => setLog(true)} />
        </div>
        <div className="sl-col is-right">
          <ShieldCard />
          <NoiseCard />
          <Stats />
        </div>
      </main>
      <FrameDetail />
      {log && <LogDrawer onClose={closeLog} />}
    </>
  );
}

export default function App() {
  const [page, setPage] = useState(route);
  useEffect(() => {
    void start();
    const on = () => setPage(route());
    window.addEventListener("hashchange", on);
    return () => {
      window.removeEventListener("hashchange", on);
      disconnect();
    };
  }, []);
  const go = useCallback((hash: string) => {
    window.location.hash = hash;
  }, []);
  return (
    <div className={`sl is-${page}`}>
      <Backdrop />
      {page === "console" ? <Console onHome={() => go("#/")} /> : <Landing onEnter={() => go("#/console")} />}
    </div>
  );
}
