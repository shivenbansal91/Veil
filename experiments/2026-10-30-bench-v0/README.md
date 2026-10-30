# 2026-10-30 — VEIL-Bench v0 validation

## Setup

- `bench/scenario.py`: editor session (`.env` with GitHub PAT, AWS key ID, Stripe key, DB password; scrolls; a Slack-token toast for two frames) and terminal session (AWS secret key, JWT in a `curl` header, password in a Postgres URL, GitHub PAT in a log line; output grows and scrolls; long lines wrap, so the JWT splits across rows). All secrets are random fakes from a seeded RNG.
- Ground truth: per frame, each secret's box and its exact glyph pixels, recorded while rendering.
- Scorer (`bench/score.py`): pixel-based, no OCR. Run with `.venv\Scripts\python -m bench.run`. Seed 1, 30 frames, 1920x1080.

## Results

| Session | Pipeline | Leaked frames | False-masked | p50 ms | p95 ms | fps |
|---|---|---|---|---|---|---|
| editor | no protection | 30/30 | 0.000 | — | — | — |
| editor | black everything | 0/30 | 1.000 | 2.72 | 9.71 | 302 |
| editor | oracle mask | 0/30 | 0.000 | 2.82 | 3.49 | 354 |
| editor | gate + oracle detector | 0/30 | 0.000 | 2.66 | 3.49 | 379 |
| terminal | no protection | 27/27 | 0.000 | — | — | — |
| terminal | black everything | 0/27 | 1.000 | 1.83 | 2.67 | 523 |
| terminal | oracle mask | 0/27 | 0.000 | 2.67 | 3.17 | 386 |
| terminal | gate + oracle detector | 0/27 | 0.000 | 2.65 | 10.19 | 311 |

"No protection" does no work, so its timings are meaningless and left out.

Seed sweep (not in the test suite): 80 runs (2 scenarios, 20 seeds, 2 lengths/sizes). No protection leaked every secret frame, the oracle leaked nothing with 0 false masking, and black-everything leaked nothing. 0 failures.

## Conclusion

- Stage 1 acceptance holds on synthetic frames: the scorer finds every planted secret in raw output and is fooled by neither blackout nor exact masking.
- The gate adds about 2.7 ms per 1080p frame (mostly the defensive pixel copy). That's negligible next to OCR (about 200 ms per strip on GPU, see `2026-10-30-ocr-benchmark`).
- Bugs the sweep found and fixed along the way: ground truth that dropped partly hidden secrets; ink guessed from colours (wrong when a box spans two backgrounds); same-coloured terminal text scrolling into an old secret's spot counted as a leak. Fixed by recording glyph pixels at render time, and by counting past secrets only via pixels the current screen can't explain.
- Not done: the *output recorder* (capturing real protected-window frames) needs Stage 2's output. Time-to-mask isn't reported separately; under the gate it is covered by leaked frames.
