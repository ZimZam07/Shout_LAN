"""
shield_test.py - Noise Shield hardware test: plain vs shielded frames under the
interference generator, through REAL sound (this laptop's speaker -> air -> mic).

Frames alternate plain / shielded (same 16-byte DATA frame), so both formats
meet the same room and the same noise. The generator really plays band-limited
noise from the speaker the whole time (the frames are mixed on top of it).

    python tools/shield_test.py --level 0.25 --frames 20
    python tools/shield_test.py --level 0                 # no generated noise
    python tools/shield_test.py --save-audio               # keep the recording for offline analysis

Prints, per format: frames decoded OK / rejected by CRC (or header) / not detected,
and the bits the shield repaired. Results are saved as JSON in results/.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shout_lan import config, frame as fr                                   # noqa: E402
from shout_lan.audio_io import Microphone, Speaker, default_devices, system_output_volume  # noqa: E402
from shout_lan.modem import Modem                                              # noqa: E402
from shout_lan.receiver import FrameReceiver                                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--level", type=float, default=config.NOISE_DEFAULT_LEVEL)
    p.add_argument("--frames", type=int, default=20, help="frames PER format")
    p.add_argument("--payload", type=int, default=5, help="payload bytes (5 -> 16-byte frame)")
    p.add_argument("--save-audio", action="store_true")
    args = p.parse_args()

    mic_dev, spk_dev = default_devices()
    modem = Modem()
    vol_start = system_output_volume()
    print(f"system output volume at start: {vol_start}; generator level {args.level}")
    mic, spk = Microphone(mic_dev), Speaker(spk_dev)
    mic.start()
    spk.open()
    rx = FrameReceiver(modem)
    stats = {False: {"ok": 0, "rejected": 0, "missed": 0, "repaired": 0, "sent": 0},
             True: {"ok": 0, "rejected": 0, "missed": 0, "repaired": 0, "sent": 0}}
    recording = []
    try:
        if args.level > 0:
            spk.set_noise(True, args.level)
        time.sleep(1.0)
        mic.read_available()
        for i in range(args.frames):
            for shield in (False, True):
                f = fr.Frame(fr.DATA, 1, 2, seq=i % 256, payload=bytes([65 + i % 26]) * args.payload,
                             flags=fr.F_FIRST | fr.F_LAST)
                raw = fr.build(f)
                spk.play(modem.modulate_bytes(raw, shield))
                time.sleep(0.4)                       # let the end of the frame reach the mic
                audio = mic.read_available()
                recording.append(audio)
                results = rx.feed(audio)
                s = stats[shield]
                s["sent"] += 1
                good = [r for r in results if r["ok"] and r["raw"] == raw]
                if good:
                    s["ok"] += 1
                    s["repaired"] += good[0]["bits_repaired"]
                    tag = f"OK (repaired {good[0]['bits_repaired']})"
                elif any(not r["ok"] for r in results):
                    s["rejected"] += 1
                    tag = "REJECTED: " + next(r["error"] for r in results if not r["ok"])
                else:
                    s["missed"] += 1
                    tag = "not detected"
                print(f"{'shield' if shield else 'plain '} frame {i:2d}: {tag}")
    finally:
        spk.set_noise(False, 0)
        time.sleep(0.3)
        spk.close()
        mic.stop()
    vol_end = system_output_volume()
    print(f"system output volume at end: {vol_end}"
          + ("" if vol_start == vol_end else "  ** CHANGED DURING THE RUN: results invalid **"))
    for shield in (False, True):
        s = stats[shield]
        print(f"{'SHIELD' if shield else 'PLAIN '}: ok {s['ok']}/{s['sent']}, rejected {s['rejected']}, "
              f"not detected {s['missed']}, bits repaired {s['repaired']}")
    print(f"speaker underflows {spk.underflows}, mic overflows {mic.overflows}")
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    path = os.path.join(ROOT, "results", f"shield_{stamp}.json")
    with open(path, "w") as fh:
        json.dump({"kind": "Measured - acoustic (single laptop, generator on this speaker)",
                   "level": args.level, "frames_per_format": args.frames,
                   "frame_bytes": fr.OVERHEAD + args.payload,
                   "system_volume_start": vol_start, "system_volume_end": vol_end,
                   "plain": stats[False], "shield": stats[True],
                   "speaker_underflows": spk.underflows, "mic_overflows": mic.overflows}, fh, indent=1)
    print("saved:", path)
    if args.save_audio:
        os.makedirs(os.path.join(ROOT, "recordings"), exist_ok=True)
        np.save(os.path.join(ROOT, "recordings", f"shield_{stamp}.npy"), np.concatenate(recording))


if __name__ == "__main__":
    main()
