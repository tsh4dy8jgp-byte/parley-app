# Safe Mode: fewer edge-tts failures

## Why

Dialogue renders fail often enough that one lesson can take about a dozen presses of Generate.
Each press only redoes the lines that are missing from the cache. Safe Mode should finish on the
first press, even if that press takes longer. It is on by default.

## What upstream reports (rany2/edge-tts, all 340 issues read on 2026-11-20)

| Failure | Issues | What Parley does today |
|---|---|---|
| "No audio was received": one voice stops answering for minutes while others work; withdrawn voices; text with nothing to say (`......`) | #443, #473, #512 (open), #446, #447, #351, #481 | 4 attempts over about 10 s, then the run stops |
| Throttling: 503 handshakes, IP blocks, worse with parallel requests and many short clips | #482, #452, #473, #512 | 4 parallel requests, no pacing |
| Silent truncation: audio cut short or words dropped, no error | #190, #230, #390 | not detected |
| 403 when Microsoft changes the token | #290, #490 | needs an edge-tts update; the pinned 7.3.1 has the October 2026 fix |

## Goals

- One press of Generate finishes a dialogue render in nearly all cases.
- When it can't, the message names the voice and the lines, says whether other voices worked,
  and says what to do next.
- The same behavior in the app and both CLIs.

## Non-goals

- A cache for plain narration, so that a failed book picks up where it stopped. This is a later
  step. Plain narration does get the pacing, the retry rounds and the length check.
- Writing our own client for the Microsoft service.
- Making batches faster.

## Anti-goals

- A false alarm that makes a line fail for good. The length check must never block a clip that
  is really complete.
- Turning Safe Mode off must not change today's behavior.

## Approaches considered

- **A. A retry policy in the speech engine (chosen).** `EdgeBackend` and `synth_all` in
  `dialog_tts/tts.py` get a `Policy`. Every path goes through them (CLI, app, batch, narration),
  so all of them get Safe Mode. It can be tested offline with failing fake backends.
- **B. Re-run the whole render from the app on failure.** This is cheap because of the cache.
  But it sends just as many requests, can't catch truncation, the CLI doesn't get it, and it
  redoes the stitching. Rejected.
- **C. Our own client for the Microsoft service.** It would see close codes, but it would have to
  copy the token code that breaks in every outage, and it would cut Parley off from upstream
  fixes. Dropped.

## Design (decided with the user)

When a voice keeps failing, Safe Mode **finishes every other line first**. Then it retries the
failed lines in up to two more rounds, with a pause before each, for about 3 minutes in total.
Then it stops and names the voice and the lines. Lines that worked are cached, so the next run
only makes the missing ones.

`Policy` (frozen dataclass), with two presets:

| Field | `FAST` (off = today) | `SAFE` |
|---|---|---|
| concurrency | 4 (from the setting / `--concurrency`) | the setting, capped at 2 |
| attempts per line per round | 4, backoff 1.5 s doubling | 3, backoff 2 s doubling |
| start_gap (min seconds between request starts, all requests) | 0 | 0.5 |
| throttle_pause (all requests wait after a 429/503; doubles up to 60 s, resets on success) | 0 | 10 |
| defer_after (a voice's remaining lines move to the next round after this many failed lines in a row) | 0 | 2 |
| stop_round_after (the whole round moves to the next one after this many failed lines in a row, any voice) | 0 | 4 |
| round_pauses | none: stop at the first failure | (60, 120) s |
| check_length | off | on |
| request deadline | none | 20 s + 1 s per 20 characters |

- **Stops at once (Safe):** HTTP 401/403 after edge-tts' own clock-skew retry, and a ValueError
  or TypeError (a bad voice name). Retrying can't fix these.
- **No internet:** if every request in round 1 fails with a connection error and none succeeds,
  Parley stops right away with "can't reach the service".
- **Length check:** a clip shorter than its text could possibly be spoken is counted as cut off
  and retried. The fastest plausible speed is 25 letters or digits per second, or 12.5 for CJK
  ideographs and Hangul, scaled by the `rate`. Duration is read with mutagen, so no ffmpeg is
  needed, and audio mutagen can't read also counts as failed. Two short results that agree within
  10% are accepted as real.
- **Lines with nothing to say:** a dialogue line with no letter or digit is a parse error in
  either mode, with a hint to use `[pause N]`. In narration, a chunk like `* * *` is skipped.
- **Waiting is visible:** before each extra round, a listener (`tts.wait_listener`, a ContextVar)
  hears `(seconds, lines, round, rounds)`. The app shows it in the status bar and the CLI prints
  it. A ContextVar keeps the engine's signatures unchanged. The app's `Runner` sets it for the
  task it starts.
- **Failure:** `SynthesisFailed(TTSError)` keeps the failed jobs and their errors.
  `render_scripts` adds "file line N". The first line of the message says what failed and what to
  do, because the app's status bar shows only the first line.
- **Switch:** `Settings.safe_mode = True`, shown as a switch at the top of Advanced →
  Performance. The CLIs get `--no-safe-mode`.

## Found while building

- A live render of `tests/fixtures/lektion_02.tagged.txt` with `--slow` took 34 requests and 52 s
  in Safe Mode, with no failures and no false cut-off alarms. Without Safe Mode it should take
  about 10–15 s (estimated, not measured).
- With only the per-voice rule, an outage of *every* voice made round 1 try 2 lines of each
  voice. A 7-voice lesson whose requests hang could take about ten minutes before the first
  pause. Hence `stop_round_after` and the shorter deadline.

## Left to the builder

- The exact wording of the messages.
- Where the speakable-text helper lives (`audiobook/chunker.py`, so the audiobook side can use
  it without importing the dialogue engine).
