# 2026-10-30 — Stage 2 pipeline: offline bench and local live smoke test

**Status: unvalidated for real-time protection (principle 9).** Everything here is on this laptop. Nothing has gone through Google Meet yet.

## Setup

- RTX 2050 4 GB, `onnxruntime-gpu` 1.31.1, `rapidocr` 3.10.0, `windows-capture` 2.0.1 (WGC), PySide6 6.12.0.
- Pipeline: `python -m engine --window ...`. That's WGC capture → `ScreenDetector` (changed rows ± 24 px as full-width bands → RapidOCR with per-character boxes → regex/entropy rules) → `Gate` (deadline 500 ms, freeze on miss, hang watchdog 5 s) → protected window (Qt, `publish()` every 66 ms).
- RapidOCR settings from `2026-10-30-ocr-benchmark`: GPU, cls off, `Det.limit_type: max` / 1920.

## 1. Offline: VEIL-Bench, OCR + rules through the gate

`python -m bench.run --ocr`, 1920x1080, 30 frames per session.

Scored with the per-character rule (see "Scorer bug" below). Scorer sanity re-run after that change: 80 runs (2 scenarios, 20 seeds, 2 sizes). No protection flags every secret frame, oracle and blackout flag none: 0 failures.

| Seed | Session | Deadline | Leaked frames | False-masked | p50 | p95 |
|---|---|---|---|---|---|---|
| 1 | editor | none | 0/30 | 0.002 | 8 ms | 3053 ms |
| 1 | editor | 500 ms | 0/30 | 0.050 | 10 ms | 2006 ms |
| 1 | terminal | none | 0/27 | 0.004 | 265 ms | 1872 ms |
| 1 | terminal | 500 ms | 0/27 | 0.045 | 267 ms | 1829 ms |
| 2 | editor | none | 0/30 | 0.002 | 10 ms | 2116 ms |
| 2 | editor | 500 ms | 0/30 | 0.048 | 9 ms | 2077 ms |
| 2 | terminal | none | 0/28 | 0.005 | 328 ms | 1981 ms |
| 2 | terminal | 500 ms | 0/28 | 0.045 | 309 ms | 2004 ms |
| 3 | editor | none | 0/30 | 0.002 | 8 ms | 2171 ms |
| 3 | editor | 500 ms | 0/30 | 0.048 | 9 ms | 2239 ms |
| 3 | terminal | none | 0/30 | 0.006 | 321 ms | 1858 ms |
| 3 | terminal | 500 ms | 0/30 | 0.047 | 507 ms | 3362 ms |
| 4 | editor | none | 0/30 | 0.002 | 13 ms | 3629 ms |
| 4 | editor | 500 ms | 0/30 | 0.049 | 8 ms | 2407 ms |
| 4 | terminal | none | 0/26 | 0.005 | 301 ms | 2138 ms |
| 4 | terminal | 500 ms | 0/26 | 0.013 | 340 ms | 2087 ms |

**Scorer bug found by the extra seeds:** under the 500 ms deadline, seeds 2–4 first scored 3–5 leaked terminal frames each, while the same detection with no deadline scored 0. Diagnosis: every flagged frame was a *freeze* showing an older approved frame, with different terminal text (same colour) where the current secret sits. About 8% of glyph pixels coincided, above the old 5%-of-pixels threshold. Nothing readable was shown. On 30 terminal frames with random same-colour text drawn in place of each secret, the old rule flagged 4 frames. Fixed: a secret leaks when 3 consecutive characters survive (≥90% of each glyph's pixels unchanged), or all of a wrapped part shorter than 3 characters. That second clause came from the new metric missing a fully visible 2-character JWT fragment. Tests cover the half-masked token, a 1.5-character sliver, the same-colour replacement, and the short fragment.

- "No deadline" isolates detection; "500 ms" is what the live gate sends. The extra false masking under the deadline is freezes: a frozen frame differs from the current screen, and the scorer counts that as hidden content.
- p50 for the editor is about 10 ms because most frames change nothing, so nothing is OCR'd. p95 is the scroll, where every row changes and the whole frame is OCR'd (1.2–3.7 s). Every scroll frame misses the deadline and the output freezes until the scroll ends.
- **Bug found by the bench:** RapidOCR read a `0` in the AWS secret key as Greek `θ`. The entropy rule was ASCII-only, so it split the token and the first 8 characters stayed unmasked (1 leaked frame). Fixed: non-ASCII characters stay inside a candidate token. Regression test: `test_ocr_lookalike_character_does_not_split_a_token`.

## 2. Live, local: recorded protected window

`bash experiments/2026-10-30-stage2-smoke/run.sh 1`:

1. `source.py` plays the bench editor session (seed 1, 1280x720, 2 fps, repaints only when the picture changes).
2. The engine protects it.
3. `record.py` captures the protected window with WGC, the way a meeting app would, for 25 s.
4. Then it records the raw source for 8 s as a positive control.
5. `check.py` OCRs every recorded picture for the planted values.

| Recording | Pictures | Black | Secrets readable (≥ 10 consecutive chars) |
|---|---|---|---|
| Protected view | 6 changes in 25 s | 1 (startup) | **0** |
| Raw source (control) | 13 changes in 8 s | 0 | 34 (all 5 kinds) |

`protected_frames.png` shows four protected pictures: black at startup, then masked editor frames, including the masked Slack token in the toast.

Per-submit trace (headless, same source):

- The first submit takes ~3 s (CUDA/model warm-up), so the output is black until then.
- Still screen: approved in 4–9 ms.
- Scroll frames: 1.1–1.8 s each, deadline missed, freeze.
- After a late frame, if the screen then stays still: retried and approved in 7–8 ms.

**Bug found live:** WGC delivers frames only when the window changes. When the last frame of a scroll missed its deadline and the window then stayed still, no new frame came, and the output stayed frozen until the next change. Fixed by retrying the last frame with a fresh timestamp after 0.2 s without a new frame. That's honest because no new frame means those pixels are still on screen. The first version of the test source repainted every 500 ms regardless and hid this; it now repaints only on change.

**Bug found while planning capture:** the gate ran the detector on the capture buffer, not on its own copy. WGC's buffer is only valid inside the callback, and a reused buffer could be overwritten mid-detection, so the boxes would describe other content than the copy being sent. The gate now detects on a read-only view of its copy.

## What this does and doesn't show

- It shows the pipeline masks every planted secret in its own output, for clean synthetic text, on this machine. The positive control shows the checker would have seen them.
- `check.py` is OCR-based, from the same model family as the detector, so it shares blind spots. Pixel-exact scoring of a real capture needs the source's geometry inside the captured window (title bar, DPI); not done.
- Not tested: real apps (VS Code, Windows Terminal), display scaling other than this machine's, a second device in a Meet call, and killing the engine process. The window disappears with the process; what Meet shows then is open.
- One source window, one seed live. Frame rate in a scroll drops to a freeze for the whole scroll.

## 3. Dry runs of the Meet procedure (`docs/stage2-meet-test.md`), local

Checked with `bench/check_recording.py`, which keeps every picture change and OCRs it for the seed-1 planted values.

| Run | Recording | Pictures | Black | Planted values readable |
|---|---|---|---|---|
| Faults, random rates (first version) | protected, 60 s | 10 | 8 | 0/5 |
| Faults, fixed schedule | protected, 60 s | 12 | 4 | 0/5 |
| Raw source (control, 8 s) | raw | 6 | 0 | 4/5 (toast not in the window) |
| Source resized 3x via `MoveWindow` | protected, 25 s | 10 | 8 | 0/5 |

- **Random faults missed cases.** The first `--faults` used random rates. In 60 s it fired 3 garbage boxes and 4 slow frames, but no crash and no hang, so a run could pass without ever exercising the watchdog. Replaced by a fixed schedule (crash, garbage, two slow every 30 analysed frames; a 7 s hang at frame 25 and then every 100). The next 60 s run fired all four kinds, including one hang.
- That the black frames during the hang come from the watchdog (rather than the following deadline miss) isn't separately confirmed from the recording; the unit test covers the watchdog path.
- **Resize:** black from the first resize until ~3 s after the last one (a fresh gate starts black; the first full read at a new size misses the deadline; the retry approves it), then masked. No tracebacks.
