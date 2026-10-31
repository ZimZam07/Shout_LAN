"""
loopback_test.py - Milestone 1 diagnostic: is the REAL acoustic link working?

It sends known random bits through the real speaker, records them with the
real microphone, decodes them and counts the bit errors (Bit Error Rate).

Examples (run from the project folder):
    python tools/loopback_test.py                      # 10 trials, 256 bits each
    python tools/loopback_test.py --trials 20 --volume 0.3
    python tools/loopback_test.py --noise 60           # count false detections in 60 s of room noise
    python tools/loopback_test.py --shared             # compare: Windows shared (processed) mic mode
    python tools/loopback_test.py --calibrate          # noise floor + tone sweep + suggested tones

Results are printed and saved as JSON in the results/ folder.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shout_lan import calibration, config                     # noqa: E402
from shout_lan.audio_io import Microphone, default_devices, record_while_playing  # noqa: E402
from shout_lan.modem import Modem                              # noqa: E402

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def save(name, data):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{name}_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print("saved:", path)


def bit_trials(args, modem, mic, spk):
    rng = np.random.default_rng(args.seed)
    trials = []
    saved_audio = []
    total_bits = total_errors = detected = 0
    for n in range(args.trials):
        bits = [int(b) for b in rng.integers(0, 2, args.bits)]
        audio, m = record_while_playing(modem.modulate(bits), mic, spk, exclusive=not args.shared)
        if args.save_audio:
            saved_audio.append((audio, bits))
        found = modem.find_preamble(audio)
        if found is None:
            trials.append({"trial": n, "preamble": False})
            total_bits += len(bits)
            total_errors += len(bits)          # a missed frame counts as all bits lost
            print(f"trial {n:2d}: preamble NOT detected (best score "
                  f"{float(modem.preamble_scores(audio).max()):.2f})")
            continue
        detected += 1
        got, conf = modem.demodulate_bits(audio, found[0], len(bits))
        errors = sum(a != b for a, b in zip(got, bits)) + (len(bits) - len(got))
        total_bits += len(bits)
        total_errors += errors
        trials.append({"trial": n, "preamble": True, "score": round(found[1], 3),
                       "errors": errors, "min_confidence": round(min(conf), 3),
                       "overflows": m.overflows})
        print(f"trial {n:2d}: preamble score {found[1]:.2f}  bit errors {errors:3d}/{len(bits)}"
              f"  min confidence {min(conf):.2f}")
        time.sleep(0.2)
    ber = total_errors / max(total_bits, 1)
    summary = {"kind": "Measured - acoustic (single-laptop loopback)",
               "mic": mic, "speaker": spk, "exclusive": not args.shared,
               "freq0": modem.freq0, "freq1": modem.freq1, "bit_time": modem.bit_time,
               "volume": modem.volume, "bits_per_trial": args.bits,
               "trials": args.trials, "preambles_detected": detected,
               "total_bits": total_bits, "bit_errors": total_errors, "ber": ber,
               "details": trials}
    print(f"\nPreambles detected: {detected}/{args.trials}")
    print(f"Bit errors: {total_errors}/{total_bits}  ->  BER = {ber:.4%}")
    save("loopback", summary)
    if args.save_audio:
        os.makedirs(os.path.join(os.path.dirname(RESULTS_DIR), "recordings"), exist_ok=True)
        path = os.path.join(os.path.dirname(RESULTS_DIR), "recordings",
                            f"bits_{datetime.now():%Y%m%d_%H%M%S}.npz")
        np.savez(path, *[a for a, _ in saved_audio], bits=np.array([b for _, b in saved_audio]))
        print("raw audio saved:", path)


def noise_test(args, modem, mic):
    """DoD B2: no false frame detections while nobody is transmitting."""
    print(f"Recording {args.noise} s of room noise (do not transmit)...")
    m = Microphone(mic, exclusive=not args.shared)
    m.start()
    time.sleep(args.noise)
    m.stop()
    audio = m.read_available()
    false_hits, pos = 0, 0
    while True:
        found = modem.find_preamble(audio, pos)
        if found is None:
            break
        false_hits += 1
        pos = found[0]
    best = float(modem.preamble_scores(audio).max()) if len(audio) else 0.0
    print(f"False preamble detections: {false_hits}   (best score {best:.2f}, threshold {modem.threshold})")
    save("noise", {"kind": "Measured - acoustic", "seconds": args.noise, "mic": mic,
                   "false_detections": false_hits, "best_score": best,
                   "threshold": modem.threshold})


def calibrate(args, mic, spk):
    exclusive = not args.shared
    rms, noise_p, _ = calibration.measure_noise(mic, exclusive)
    tones = calibration.run_sweep(mic, spk, exclusive, args.volume)
    snr = calibration.snr_table(tones, noise_p)
    print(f"Noise floor RMS: {rms:.5f}")
    for f in sorted(snr):
        print(f"  {f:5d} Hz  SNR {snr[f]:6.1f} dB  " + "#" * max(0, int(snr[f] / 2)))
    # Final choice by real bit-error tests, not by the sweep alone.
    ranking = []
    for f0, f1 in calibration.candidate_pairs(snr):
        modem = Modem(freq0=f0, freq1=f1, bit_time=args.bit_time, volume=args.volume)
        errors, total, det = calibration.ber_test(modem, mic, spk, exclusive)
        ranking.append({"pair": [f0, f1], "bit_errors": errors, "bits": total, "detected": det})
        print(f"  BER test {f0}/{f1} Hz: {errors}/{total} errors, preambles {det}/3")
    ranking.sort(key=lambda r: r["bit_errors"])
    print("Recommended BFSK tones:", ranking[0]["pair"])
    save("calibration", {"kind": "Measured - acoustic", "noise_rms": rms,
                         "snr_db": {str(k): round(v, 1) for k, v in snr.items()},
                         "ber_ranking": ranking, "recommended": ranking[0]["pair"],
                         "volume": args.volume})


def main():
    p = argparse.ArgumentParser(description="SHOUT LAN acoustic loopback diagnostics")
    p.add_argument("--trials", type=int, default=10)
    p.add_argument("--bits", type=int, default=256)
    p.add_argument("--volume", type=float, default=config.TX_VOLUME)
    p.add_argument("--f0", type=int, default=config.FREQ_0)
    p.add_argument("--f1", type=int, default=config.FREQ_1)
    p.add_argument("--bit-time", type=float, default=config.BIT_TIME)
    p.add_argument("--mic", type=int, default=None)
    p.add_argument("--speaker", type=int, default=None)
    p.add_argument("--shared", action="store_true", help="use Windows shared (processed) mic mode")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--noise", type=float, default=0, help="seconds of false-detection test")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--save-audio", action="store_true", help="save every trial's recording + bits")
    args = p.parse_args()

    mic, spk = default_devices()
    mic = args.mic if args.mic is not None else mic
    spk = args.speaker if args.speaker is not None else spk
    modem = Modem(freq0=args.f0, freq1=args.f1, bit_time=args.bit_time, volume=args.volume)
    print(f"mic={mic} speaker={spk} exclusive={not args.shared} tones={args.f0}/{args.f1} Hz "
          f"bit={args.bit_time * 1000:.0f} ms volume={args.volume}")

    if args.calibrate:
        calibrate(args, mic, spk)
    elif args.noise:
        noise_test(args, modem, mic)
    else:
        bit_trials(args, modem, mic, spk)


if __name__ == "__main__":
    main()
