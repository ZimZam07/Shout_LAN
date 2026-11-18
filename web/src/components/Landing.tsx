import { useEffect } from "react";
import { ArrowRight, Laptop, RotateCw, ShieldCheck, Stamp, Volume2 } from "lucide-react";
import { Mark } from "./Shell";

/* The opening moment: what this is, in one breath, and one way in. The two
 * laptops and their beeps here are an illustration (labelled as one), never
 * data. Enter, Space or the button opens the console. */

const BEADS = [1, 0, 1, 1, 0, 1, 0, 0, 1, 0, 1, 1];

export function Landing({ onEnter }: { onEnter: () => void }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Enter") onEnter();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onEnter]);

  return (
    <main className="sl-landing">
      <header className="sl-landing-bar">
        <span className="sl-dock-mark is-static">
          <Mark />
        </span>
        <b>Shout LAN</b>
        <span>a Computer Networks project</span>
      </header>

      <div className="sl-landing-copy">
        <h1>
          A network
          <br />
          you can hear
        </h1>
        <p>
          Two laptops, no Wi-Fi, no cable. Messages cross the room as beeps, so a lost or damaged one is something you can hear, and see.
        </p>
        <button type="button" className="sl-enter" onClick={onEnter} autoFocus>
          Open the console <ArrowRight aria-hidden strokeWidth={2} />
        </button>
      </div>

      <figure className="sl-how" aria-label="Illustration: one laptop beeping bits to another">
        <span className="sl-node-disc">
          <Laptop aria-hidden strokeWidth={1.3} />
        </span>
        <div className="sl-how-air" aria-hidden="true">
          <div className="sl-how-parcel">
            <div className="sl-parcel-box">
              <div className="sl-parcel-words">
                <span className="sl-parcel-text">HELLO</span>
                <span className="sl-parcel-tech">a parcel of beeps</span>
              </div>
              <span className="sl-seal is-plain">
                <Stamp strokeWidth={1.6} />
              </span>
            </div>
          </div>
          <div className="sl-how-lanes">
          {BEADS.map((b, i) => (
            <i key={i} className={`is-${b}`} style={{ animationDelay: `${i * -0.28}s` }} />
          ))}
          </div>
        </div>
        <span className="sl-node-disc">
          <Laptop aria-hidden strokeWidth={1.3} />
        </span>
        <figcaption>illustration · high beep = 1, low beep = 0, fifty a second</figcaption>
      </figure>

      <ol className="sl-steps">
        <li>
          <span className="sl-stat-mark" aria-hidden="true"><Volume2 strokeWidth={1.5} /></span>
          <div>
            <b>Say it</b>
            <span>Text becomes a parcel of beeps with a seal on it.</span>
          </div>
        </li>
        <li>
          <span className="sl-stat-mark" aria-hidden="true"><RotateCw strokeWidth={1.5} /></span>
          <div>
            <b>Break it</b>
            <span>Noise breaks the seal. No receipt comes back, so it is sent again.</span>
          </div>
        </li>
        <li>
          <span className="sl-stat-mark" aria-hidden="true"><ShieldCheck strokeWidth={1.5} /></span>
          <div>
            <b>Shield it</b>
            <span>Spare bits let a damaged parcel repair itself on arrival.</span>
          </div>
        </li>
      </ol>
    </main>
  );
}
