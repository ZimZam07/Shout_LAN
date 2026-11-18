# Windows node 2 report (2026-11-18 evening)

Windows laptop alone (speaker to its own microphone), Windows volume 70 %, other audio apps closed.
Full details and tables: `docs/TESTING.md`, section "Windows node 2: start-up hang and transmit-path check".

## Problem 1: node hung at start-up — FIXED
- **Cause (measured):**
  - When the exclusive speaker stream was started right after the exclusive microphone, the playback device sometimes never began taking samples. The stream reported "active", but its buffer never drained, so the blocking warm-up `write` in `Speaker._open_stream` waited forever.
  - Standalone reproduction: 8 / 26 hangs with mic-then-speaker, 0 / 11 with speaker-first, 0 / 11 with a 1–3 s pause between the two.
- **Not the cause:**
  - Two competing nodes: each pair of `shout.py` PIDs was one node. The venv `python.exe` is a launcher that starts the real interpreter as a child.
  - Commits `e9f2f19` / `3f50935`: neither touches device opening, and the warm-up was already in `e3cb3f1`.
- **Fix (`d6ec1ae`):**
  - Open the speaker first, then the microphone (`AcousticChannel.start`, `AcousticLoopbackHub.attach`).
  - The warm-up writes only as fast as the device takes it, with a 2 s deadline.
  - Exclusive mode is retried 3 times, then shared mode with the existing warning. If that fails too, a clear `AudioError` produces the `ERROR:` line from `shout.py`.
  - Platform-neutral: on the Mac it only changes the open order and the warm-up loop.
- **Before / after:**
  - Before: the node hung (`running: false`, `speaker_mode: "closed"`).
  - After: 6 / 6 consecutive starts gave `engine_started`, READY, `running: true` and `speaker_mode: "exclusive"`.

## Problem 2: transmitted frames corrupted in the first byte — CAUSE FOUND, NOT FIXED
- **Hypothesis "idle persistent stream drops or inserts a buffer": not supported.**
  - Cross-correlating each transmitted waveform against the recording shows a constant lag (about 1 sample) through every frame, with no jumps.
  - Both arq runs had 0 speaker underflows and 0 microphone overflows.
- **Measured cause: inter-symbol interference.** The 2500 Hz tone keeps ringing, so a lone 3500 Hz bit after a run of 2500 Hz bits has about 0 dB margin.
  - Every frame starts with the same on-air bits `0 0 0 1 0 0 1 0`, because the scrambler seed is fixed. Bits 3 and 6 are exactly that case, so the first byte fails in a consistent place on every frame.
  - 2500 Hz bits: 0 of 859 below 0 dB. 3500 Hz bits after 3 or more 2500 Hz bits: median −0.5 to 0.8 dB, 45 below 0 dB.
- **Numbers (current code):**
  - Loopback, fresh stream: 10/10 preambles, BER 0.74 % (19/2560).
  - `arq_test --quick`: 3/3 and 3/3. The first run had 4 of 11 transmissions rejected and recovered by ARQ; the second had none beyond the injected test corruption.
  - So the brief's targets (BER ≤ 1 % with 10/10, and arq 3/3 twice) were already met **before any Problem 2 change**. No before/after pair exists for Problem 2.
- **Offline option, not applied:** stronger receiver echo cancelling (`ECHO_CANCEL` 0.30 with `WIN_LO` 0.7, against 0.15 / 0.5 now).
  - On this laptop's recordings: 8/11 → 11/11 clean frames, and loopback bit errors 19 → 5.
  - On an October recording: 31/44 → 25/44 clean frames.
  - The receiver is shared with the Mac, so the defaults were left unchanged. It should be checked on recordings from the Mac's microphone before being adopted.
- **Not applied:** "keep the mixer writing silence between frames" targets buffer loss while idle, which was not observed.

## Tests
- `pytest -q`: **143 passed, 0 failed** (279.99 s), including 3 new tests in `tests/test_speaker_start.py`.

## Commits
- `d6ec1ae`: start-up fix, tests, TESTING.md section, measurement JSONs.
- The commit that adds this report.

## Not verified
- The Windows → Mac and Mac → Windows links after the fix: there was no two-laptop run.
- The effect of the open-order change on the Mac (Core Audio): expected to be neutral, but not run there.
- The receiver echo-cancel change on any Mac recording.
