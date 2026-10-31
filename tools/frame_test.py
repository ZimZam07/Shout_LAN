"""
frame_test.py - Milestone 2 hardware test: real frames through speaker -> air -> microphone.

Single laptop (loopback):
    python tools/frame_test.py                       # 10 x "HELLO" frames + one 3-fragment message
Two laptops:
    laptop B:  python tools/frame_test.py --listen 120     # print every frame heard for 120 s
    laptop A:  python tools/frame_test.py --send-only      # just transmit the test frames

The microphone runs continuously and audio is fed to the receiver in small
chunks - exactly as the real engine does.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shout_lan import config, frame as fr                      # noqa: E402
from shout_lan.audio_io import Microphone, Speaker, default_devices  # noqa: E402
from shout_lan.modem import Modem, bytes_to_bits                # noqa: E402
from shout_lan.receiver import FrameReceiver                    # noqa: E402

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
LONG_MESSAGE = b"HELLO FROM COMPUTER A - THIS MESSAGE NEEDS THREE FRAMES!"


def test_frames(trials):
    frames = [fr.Frame(fr.DATA, src=1, dst=2, seq=i, payload=b"HELLO",
                       flags=fr.F_FIRST | fr.F_LAST) for i in range(trials)]
    for i, (piece, flags) in enumerate(fr.fragment(LONG_MESSAGE)):
        frames.append(fr.Frame(fr.DATA, src=1, dst=2, seq=100 + i, payload=piece, flags=flags))
    return frames


RECORDED = []          # every chunk fed to the receiver (saved with --save-audio)


def collect(mic, rx, seconds):
    end = time.time() + seconds
    out = []
    while time.time() < end:
        audio = mic.read_available()
        RECORDED.append(audio)
        out += rx.feed(audio)
        time.sleep(0.05)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=10)
    p.add_argument("--volume", type=float, default=config.TX_VOLUME)
    p.add_argument("--listen", type=float, default=0, help="only receive, for N seconds")
    p.add_argument("--send-only", action="store_true")
    p.add_argument("--mic", type=int)
    p.add_argument("--speaker", type=int)
    p.add_argument("--save-audio", action="store_true", help="keep the raw recording in recordings/")
    args = p.parse_args()

    mic_dev, spk_dev = default_devices()
    mic_dev = args.mic if args.mic is not None else mic_dev
    spk_dev = args.speaker if args.speaker is not None else spk_dev
    modem = Modem(volume=args.volume)
    speaker = Speaker(spk_dev)
    frames = test_frames(args.trials)

    if args.send_only:
        for f in frames:
            raw = fr.build(f)
            print(f"sending seq {f.seq:3d} {f.payload!r} ({len(raw)} bytes, "
                  f"{modem.airtime(len(raw) * 8):.2f} s)")
            speaker.play(modem.modulate_bytes(raw))
            time.sleep(0.5)
        return

    mic = Microphone(mic_dev)
    mic.start()
    rx = FrameReceiver(modem)
    results = []
    try:
        if args.listen:
            print(f"Listening for {args.listen} s ...")
            results = collect(mic, rx, args.listen)
        else:
            time.sleep(0.3)
            for f in frames:
                raw = fr.build(f)
                t0 = time.time()
                speaker.play(modem.modulate_bytes(raw))
                results += collect(mic, rx, 0.4)
                print(f"sent seq {f.seq:3d} ({len(raw)} B) in {time.time() - t0:.2f} s "
                      f"(theory {modem.airtime(len(raw) * 8):.2f} s)")
    finally:
        mic.stop()
    if args.save_audio and RECORDED:
        import numpy as np
        os.makedirs("recordings", exist_ok=True)
        np.save(f"recordings/frames_{datetime.now():%Y%m%d_%H%M%S}.npy", np.concatenate(RECORDED))
    print(f"microphone overflows: {mic.overflows}")

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    for r in results:
        if r["ok"]:
            f = r["frame"]
            print(f"  RX OK   seq {f.seq:3d} src {f.src} dst {f.dst} {f.flag_names()} "
                  f"payload {f.payload!r} CRC valid")
        else:
            print(f"  RX BAD  {r['error']}")

    reasm = fr.Reassembler()
    message = None
    for r in ok:
        if r["frame"].seq >= 100:
            message = reasm.add(r["frame"].payload, r["frame"].flags) or message
    hello_ok = sum(1 for r in ok if r["frame"].payload == b"HELLO")
    summary = {"kind": "Measured - acoustic (single-laptop loopback)" if not args.listen
               else "Measured - acoustic (listen mode)",
               "hello_frames_sent": 0 if args.listen else args.trials,
               "hello_frames_valid": hello_ok, "valid_frames": len(ok),
               "crc_or_format_failures": len(bad),
               "errors": [r["error"] for r in bad],
               "long_message_reassembled": message == LONG_MESSAGE,
               "volume": args.volume}
    print(json.dumps({k: v for k, v in summary.items() if k != "errors"}, indent=1))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"frames_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w") as fh:
        json.dump(summary, fh, indent=2)
    print("saved:", path)


if __name__ == "__main__":
    main()
