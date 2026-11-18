# Brief for the Windows laptop (node 2)

You are fixing the Windows side of SHOUT LAN for a live demo tomorrow. This laptop is node 2 (Windows, WASAPI); the other laptop is a MacBook running node 1. Both run the branch `demo-dashboard` of this repo. Work on this laptop only, and get it right in one pass: diagnose with evidence before changing code, verify with measurements after.

This brief replaces the earlier "whenever I type go" instruction: you may now change code, within the rules below.

## Background (read first)

- `docs/DASHBOARD_API.md`: how the new launcher `shout.py` and the dashboard work.
- `shout_lan/audio_io.py` (Speaker, Microphone, the speaker "mixer" thread, NoiseGenerator, ClickGenerator), `shout_lan/channel.py`, `shout_lan/engine.py` (`start()`, `_tx_loop`), `shout.py`.
- `docs/TESTING.md`, section "M6" and the later link sections: the history of sound problems on this exact laptop.

## Problem 1 (blocking): the node does not start on this laptop with the current code

Evidence: with the latest `demo-dashboard`, `python shout.py --node 2` serves the web page on `127.0.0.1:8082`, but `/api/status` reports `"running": false` and `"speaker_mode": "closed"`, no `engine_started` event is ever emitted, and a queued message never leaves "queued" (`results/win_task_200114.json`). `engine.start()` calls `channel.start()` first, so start-up appears to stop while opening the microphone or speaker.

The older commit `e3cb3f1` on this branch DID start here (`engine_started`, `speaker_mode` "exclusive", it received a frame and played an ACK; `results/win_events.jsonl`). Changes since then: `e9f2f19` (clicks interference generator; changes in `audio_io.py` and `channel.py`) and `3f50935` (a 0.35 s turnaround gap in `engine.py`).

What the stack dump already shows (`results/win_task_200516.json`, key `win_stacks_200516.txt`): the main thread is stuck inside `Speaker._open_stream` at the blocking warm-up `stream.write(...)` (`audio_io.py` line 298, called from `open()` line 313, the exclusive-mode attempt, from `channel.start()`). So the exclusive output stream opens, but the first blocking write never returns.

The same file shows the process list at that moment: TWO `shout.py --node 2` processes were alive before the restart (PIDs 16060 and 9948), and two copies of `tools/win_task.py` were running at once, which then started two fresh nodes at the same time (PIDs 1996 and 22416). Two nodes competing for one sound device in exclusive mode is a likely cause of the stuck write, but that is not proven: first reproduce with exactly one node process and nothing else using audio, and only then decide whether there is also a code fault. Either way the node must not hang silently: a start-up that cannot get the speaker should end with one clear `ERROR:` line (or fall back to shared mode with the existing visible warning), never a web page in front of a dead engine. Consider also refusing to start when another node is already running on the same ports.

If newer `results/win_stacks_*.txt` or `results/win_node_*.log` exist, read them too: they are a thread stack dump and the console log of a fresh start. You can produce them with `.venv\Scripts\python tools\win_task.py` (it stops every node process, starts a fresh node, and dumps all thread stacks after 25 s).

Find the real cause. Stale python processes still holding the sound device in exclusive mode are one candidate: check for and stop every old `shout.py` / `run_engine.py` process before concluding anything. Fix it and prove the node starts: `engine_started` emitted, `"running": true`, `speaker_mode` "exclusive".

## Problem 2: frames this laptop TRANSMITS arrive corrupted in a consistent way

Evidence from the Mac, with commit `e3cb3f1` running here: the Mac detected the start chirp of 6 out of 6 frames sent by this laptop (scores 0.42-0.46) and rejected all 6 inside the very first byte. The first 4 bits were right every time and bits 5-8 were wrong every time (sync byte 0x7E decoded as 0x77, 0x7A, 0x7F, 0x77, 0x73, 0x75). The same position failing on every frame is not random noise. It suggests the sound goes out of step roughly 0.2 s into each transmission.

Hypothesis, NOT yet proven: the long-lived speaker stream sits idle between frames and glitches (drops or inserts a buffer) shortly after playback resumes. This fits the project's own history in `docs/TESTING.md`: on this laptop a stream opened fresh for each trial was error-free, while the engine's persistent stream gave corrupted frames (`arq_test` 0/3), and earlier work measured timing jumps of whole audio buffers.

Test it on this laptop alone, speaker to its own microphone, no Mac needed:

```
.venv\Scripts\python -u tools\loopback_test.py --trials 10 --save-audio     (fresh stream per trial)
.venv\Scripts\python -u tools\arq_test.py --quick --save-audio              (two engines through the persistent stream: the real engine path)
```

Use the saved recordings to locate where in a frame the bit errors start and whether there is a timing jump (correlate the recording against the transmitted waveform, as TESTING.md M6 describes). Then fix the cause.

One candidate fix to evaluate, if the hypothesis holds: never let the output stream run dry, by keeping the existing mixer thread running all the time and writing silence between frames (today it only runs while the interference generator is on). Whatever you choose, show before/after numbers from the same tests.

Target: loopback BER <= 1% with 10/10 chirps detected, and `arq_test.py --quick` 3/3, twice in a row.

## Rules

- Before any sound test: ask the person at this laptop to close every app that can play audio and to set the Windows volume to 70%, and wait for the answer. Do not change the volume during a run.
- Do not change the on-air format (frame layout, preambles, Hamming code, bit timing), the HTTP/SSE contract, or anything under `web/`. The Mac must keep interoperating.
- Keep changes platform-neutral, or gate Windows-only behaviour on the WASAPI host API the way `audio_io.py` already does. The Mac runs Core Audio and currently works; do not alter what happens there unless the change is clearly safe on both.
- Run the silent test suite before pushing and report exact counts: `.venv\Scripts\python -m pytest -q`. The test `tests/test_shield.py::test_shield_switched_on_while_a_plain_message_is_stuck_gets_it_through` is known to be timing-flaky under load; re-run it alone before treating it as a failure.
- No invented numbers. Say plainly what you measured, what you inferred, and what you could not verify.
- Commits: plain descriptive messages, no co-author lines, no mention of AI tools. `git pull --rebase origin demo-dashboard` before pushing, then `git push origin demo-dashboard`.
- Append a short dated section to `docs/TESTING.md` with the cause, the fix and the before/after measurements.

## When you finish

Leave the node running (`.venv\Scripts\python shout.py --node 2`) and write `results/win_report.md` with: the cause and fix for each problem, the before/after numbers, the pytest counts, the commit hashes, and anything still unverified. Commit and push it with the rest, and give the same summary in your reply.
