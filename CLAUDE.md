# VEIL — Project Brief

> **Purpose of this file:** complete context for anyone (human or AI assistant) working on VEIL. Read this fully before writing code or proposing designs. If something here conflicts with a later decision, update this file — it is the source of truth.

---

## 1. What VEIL is

**One line:** VEIL is a local-first privacy engine for screen sharing that masks secrets before a frame ever reaches the meeting, and measures what actually escapes.

**The problem.** During a Google Meet / Zoom / Teams screen share, sensitive content appears on screen unexpectedly: an API key in a `.env` file, a token printed in a terminal, a credential on a cloud dashboard, a chat notification. Once a frame is transmitted, it is gone — blurring it 300 ms later doesn't help.

**What VEIL does.** The user sees their normal screen. Meeting participants see a *protected* version: every outgoing frame passes through VEIL's detection and masking pipeline first, and if processing can't complete in time, VEIL refuses to send an unprocessed frame.

**What makes it more than "OCR + rectangles":**
1. **Fail-closed delivery** — no frame bypasses the processing gate, including during startup, errors, and timeouts.
2. **Layered detection** — source-aware hints where available, OCR/vision as fallback, policy as the final gate.
3. **Registered-secret matching** — matching on-screen text against the user's *actual* secrets, robust to OCR errors (the core ML contribution).
4. **VEIL-Bench** — a reproducible benchmark that measures leakage in the *outgoing stream*, not just detector accuracy.

**Owner:** Shiven (Computer Engineering, TIET). Target hardware: Windows laptop with an **NVIDIA RTX 2050 (4 GB VRAM)**. All performance claims must be measured on this machine.

---

## 2. Non-goals (for now)

- Not a cloud service. Nothing leaves the machine. No LLM/API calls in the pipeline.
- Not cross-platform in V1. **Windows only.**
- Not integrating with every meeting app at once. V1 = one protected sharing route, verified with Google Meet.
- Not claiming to catch every possible secret. See §4.
- Not modifying Meet/Zoom/Teams clients or using unsupported internals beyond what Stage 0 validates.

---

## 3. Threat model

**Protecting against:** anyone who sees the *outgoing video* — meeting participants, and anyone viewing a recording of the meeting.

**Out of scope:** malware or an attacker with access to the local machine. Such an attacker can read `.env` files directly, so holding secrets in memory during matching is acceptable.

**Assets:** API keys, tokens, passwords, credentials, private keys, connection strings, personal messages/notifications, any user-registered secret, and any window the user marks private.

This threat model settles most design arguments. If a proposal is debated, check it against this section first.

---

## 4. Core principles (non-negotiable)

1. **Fail-closed delivery ≠ perfect detection.** Keep these distinct everywhere — code, docs, UI, resume.
   - *Fail-closed delivery*: VEIL never transmits a frame that skipped the required processing stages. This is engineered and tested.
   - *Detection*: VEIL finds the secrets it can. A fully processed frame can still contain an unknown secret. This is measured, never guaranteed.
2. **A stale safe frame beats a fresh unsafe one.** If the current frame isn't ready by the deadline, re-send the last *approved* frame (freeze) or a blackout frame. Never the raw frame.
3. **Opaque masks only.** Solid fill (optionally labelled `[REDACTED]`). No blur, pixelation, or anything reversible-looking.
4. **Never trust a stale mask.** If content under a masked region changes, that region must be re-analyzed before the mask is considered valid for the new content.
5. **Local-first.** All processing on-device.
6. **Measure, don't assume.** Every performance or accuracy claim comes from VEIL-Bench or a logged experiment.
7. **Honest language.** The leak report says "No leakage detected in tested output", never "nothing leaked".
8. **Prove one route before adding more.** No parallel development of five integrations.
9. **Real-time protection is not validated until the actual outgoing stream is tested.** Unit tests, offline bench runs, and local previews validate components. Only a test of what the receiver actually gets (a second device in the Meet call, or a recording of it) validates protection. Until then, say "unvalidated".

---

## 5. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ CAPTURE ADAPTERS                                             │
│  Windows window capture (WGC) · monitor capture (DXGI)       │
│  · browser track (only if Stage 0 passes)                    │
└───────────────────────────┬──────────────────────────────────┘
                            │ raw frames (never leave the engine)
┌───────────────────────────▼──────────────────────────────────┐
│ DETECTION & EVIDENCE ENGINE                                  │
│  L0 source hints · L1 accessibility text · L2 vision (OCR,   │
│  secret classifier, registry matcher, sensitive-UI detector) │
│  · change detection / region tracking                        │
└───────────────────────────┬──────────────────────────────────┘
                            │ evidence (regions + confidence + source)
┌───────────────────────────▼──────────────────────────────────┐
│ POLICY & FRAME GATE                                          │
│  mask fusion · per-frame deadline · default-deny fallback    │
│  · private-app rules · audit log                             │
└───────────────────────────┬──────────────────────────────────┘
                            │ approved frames only
┌───────────────────────────▼──────────────────────────────────┐
│ PROTECTED OUTPUT                                             │
│  Protected window (primary) · browser track (if verified)    │
│  · virtual camera (later)                                    │
└───────────────────────────┬──────────────────────────────────┘
                            │
                 Google Meet / Zoom / Teams

  VEIL-Bench taps the PROTECTED OUTPUT side to measure what escaped.
```

### 5.1 Frame lifecycle

Every captured frame gets an ID and timestamp, and moves through states:

`CAPTURED → ANALYZING → MASKED → APPROVED → PUBLISHED`

with failure exits:

- `DEADLINE_MISSED` → publish last approved frame (freeze) or blackout
- `ERROR` (any stage throws) → blackout, log, keep running
- `POLICY_DENY` (e.g. private app in focus) → placeholder card

Only frames in `APPROVED` may reach an output adapter. The output adapter must have **no code path** to access raw frames — enforce this structurally (separate types/queues), not by convention.

### 5.2 The gate

- Per-frame deadline (initial target: ~500 ms end-to-end; tune with measurements). Screen sharing tolerates low frame rates (Meet often sends screen shares at roughly 5–15 fps), which is what makes a gated pipeline feasible.
- Startup: output shows a blackout/"VEIL starting" card until the first frame is approved.
- Shutdown/crash: the protected window must show blackout, never the last raw capture. Test this explicitly (kill the engine mid-session).

### 5.3 Change detection and tracking

- Split frames into tiles; hash each tile; re-analyze only changed tiles.
- **Expand dirty tiles to full text lines before OCR.** Tile hashes only say *where* something changed. Never OCR raw tiles: a token that crosses a tile edge gets split, and no rule or matcher fires on either half. Grow each dirty region to the full text lines it touches (full line width, plus a margin of one line above and below for wrapped secrets), then OCR that.
- For full-monitor capture, DXGI Desktop Duplication provides dirty rects and move rects (useful for scrolling — shift existing masks by move rects, then verify).
- For window capture (WGC), compute tile diffs ourselves.
- A mask carried forward by tracking is provisional until the region is re-verified (principle 4).

---

## 6. Detection layers

| Layer | Source | Covers | Notes |
|---|---|---|---|
| **L0 — Source hints** | App integrations (VS Code extension, browser DOM, later terminal) | Editors, web pages, password fields | Most accurate; app knows what's secret before rendering. Needs coordinate mapping (DOM/editor → captured pixels: scroll, zoom, DPI scaling, window position). |
| **L1 — Accessibility text** | Windows UI Automation | Native apps exposing text | Faster/more accurate than OCR where available. Later stage. |
| **L2 — Vision** | OCR + rules + classifier + registry matcher + sensitive-UI detector | Everything | Probabilistic fallback. |
| **L3 — Policy** | User config + gate | Private apps, low confidence, unknown state | Default-deny when unsure. |

**Fusion:** union of all mask regions above their thresholds. When layers agree, confidence increases; disagreement is logged for analysis. Masking errs toward over-masking.

### 6.1 L2 detail

- **OCR:** PaddleOCR or RapidOCR (ONNX Runtime, CUDA). Evaluate both on the RTX 2050.
- **Rules:** regex + entropy rules for known secret formats (AWS keys, GitHub tokens, Stripe keys, JWTs, private-key headers, connection strings, etc.). Gitleaks' rule set is a good reference.
- **Registry matcher:** see §7.
- **Sensitive-UI detector (later):** small object detector for notification toasts, chat panes, password fields — trained on synthetic screenshots.

Known hard cases to design for: secrets split across lines/wraps, partially scrolled text, small fonts, high-DPI scaling, dark themes, syntax highlighting, moving windows, toasts that appear for <1 s.

---

## 7. Secret registry & OCR-robust matching (core ML contribution)

**Idea:** generic detection asks "does this look like a secret?" The registry also asks "is this one of *my* secrets?" — which works even for unknown token formats.

**Example:** registered `sk_live_ABC123XYZ`, OCR reads `sk_live_ABC12BXYZ`. Exact matching fails; an OCR-aware matcher should still flag it.

### 7.1 Storage (security)

- **Encrypted local registry**, key protected with **Windows DPAPI** (`CryptProtectData`, via pywin32) or Windows Credential Manager.
- Decrypted only into memory for matching; never written to logs, benchmark outputs, or crash dumps.
- **Do NOT store salted n-gram hashes** of secrets — short fragments are low-entropy and guessable regardless of salt. Any future keyed-fingerprint / privacy-preserving approximate matching must come with a written security analysis.
- Sources: user adds secrets manually, or imports from chosen `.env` files.

### 7.2 Matcher — must be an ablation, not an assumption

Compare on VEIL-Bench:
1. **Rules-only** baseline (regex + entropy)
2. **Plain fuzzy** (Levenshtein / partial ratio)
3. **OCR-confusion-weighted edit distance** (cheap substitution costs for O↔0, l↔1↔I, B↔8, S↔5, rn↔m, etc.) — a strong baseline
4. **Learned matcher** trained on synthetic secrets with simulated OCR corruption (character confusions, drops, insertions, line breaks, truncation)

Also handle **partial visibility**: a secret cut by a line wrap or window edge should still match on its visible portion.

If a simpler method wins, that's a valid result — report it.

**Training data:** synthetic secrets only. Corruption generated by (a) rendering secrets in real fonts/themes and running real OCR, and (b) a corruption model fitted to those observed errors. No real user credentials in any dataset.

---

## 8. Output routes

1. **Protected window (primary, V1).** A VEIL window that displays approved frames. User shares it in Meet via *Present now → A window → VEIL*. Must not be the window VEIL is capturing (avoid recursion). Must show blackout on any failure.
2. **Browser track (conditional on Stage 0).** A Chrome MV3 extension with a content script in the `MAIN` world at `document_start` wraps `navigator.mediaDevices.getDisplayMedia`, and returns a processed track (`MediaStreamTrackProcessor` + `MediaStreamTrackGenerator`/`VideoTrackGenerator`). Risks: iframes (`all_frames`, `match_origin_as_fallback`), fail-open on wrapper errors, track metadata/stop/audio behavior, Meet changes, API compatibility. **The product is not designed around this until Stage 0 proves it.** Plan: [docs/stage0-plan.md](docs/stage0-plan.md).
3. **Virtual camera (later).** Via `pyvirtualcam` / OBS virtual camera driver for desktop Zoom/Teams. Meet treats it as a camera, not a presentation.

---

## 9. VEIL-Bench

The benchmark is a first-class deliverable, built **before** the detection gets clever.

### 9.1 Components

- **Scenario generator:** scripted desktop sessions (VS Code with `.env`, terminal printing tokens, browser dashboards, scrolling code, notification toasts) with **planted synthetic secrets** and ground-truth timing/positions.
- **Output recorder:** captures the *protected output* frames (what Meet would receive).
- **Scorer:** checks each output frame for readable planted secrets, compares masks to ground truth, computes metrics.
- **Fault injection:** kill/hang the engine, slow OCR artificially, spike GPU load — verify fail-closed behavior.

**Ground truth is pixel-based wherever possible.** Scenarios record the exact pixel rectangle of every planted secret in every frame (rendered by us, so geometry is known). The scorer checks the output pixels inside those rectangles: is the rectangle fully covered by the opaque mask fill, or does any original pixel survive? This is exact and doesn't depend on OCR. OCR-based readability scoring (OCR + registry match against ground truth) is a secondary check, and the fallback only where geometry isn't known (e.g. real third-party app renders). A scorer that relies only on OCR shares the detector's blind spots: a secret our OCR can't read also gets scored as "not leaked".

### 9.2 Metrics (keep them separate)

| Metric | What it establishes |
|---|---|
| Unprocessed frames transmitted | Was the gate ever bypassed? (target: 0) |
| Readable planted-secret frames | How often detection missed (leaked-frame rate) |
| Time-to-mask | Delay from secret appearing to output masked (should be 0 visible frames under the gate) |
| False-masked area | How much legitimate content was hidden |
| End-to-end latency & output fps | Cost of protection on RTX 2050 |
| Behavior under faults | Do crashes/timeouts ever expose raw frames? |

### 9.3 Baselines

No protection · rules-only · OCR + rules · full VEIL (fused layers) · each matcher variant from §7.2.

---

## 10. Product features

- **Rehearsal mode** — record a dry run of a demo; VEIL analyzes it offline and reports risks with timestamps ("terminal showed an AWS-format key at 0:42"). No real-time constraints; reuses the engine. Early, high-value demo.
- **Leak report** — after each session: timeline of masks, freezes, blackouts, low-confidence moments, and gate stats. Wording per principle 7.
- **Private apps** — user-designated apps are replaced by a placeholder card whenever they appear in captured content (policy layer).
- **Control UI** — select source window, start/stop protection, manage registry, view reports. Tray icon + small window.

---

## 11. Tech stack (initial — revisit with measurements)

| Area | Choice | Notes |
|---|---|---|
| Engine language | Python 3.13 (installed); 3.11+ is fine | Fast iteration; hot paths may move to Rust/C++ later if profiling demands. Check 3.13 wheels exist for `windows-capture`, `dxcam`, `onnxruntime-gpu` before Stage 2 |
| Window capture | `windows-capture` (WGC bindings) | Single-window capture |
| Monitor capture | `dxcam` (DXGI Desktop Duplication) | Dirty/move rects |
| OCR | RapidOCR / PaddleOCR on ONNX Runtime (CUDA) | Benchmark both |
| Image ops | OpenCV, NumPy | |
| Protected window + control UI | PySide6 (Qt) | Single process for V1; React/Tauri UI later if useful |
| ML training | PyTorch or TensorFlow/Keras | Small matcher model; export to ONNX |
| Secret storage | DPAPI via pywin32 | |
| Browser extension | TypeScript, Chrome MV3 | Stage 0 only, at first |
| VS Code extension | TypeScript | Stage 5 |
| Engine ↔ adapters IPC | Local WebSocket (localhost only, auth token) | |
| Tests | pytest; benchmark runs scripted and reproducible | |
| Lint/format | ruff | |
| Secret scanning | gitleaks pre-commit hook | See §12.1 |

---

## 12. Proposed repo layout

Target layout. Create a directory only when its stage starts (§13), not before.

```
veil/
├── CLAUDE.md                 # this file
├── README.md
├── .githooks/pre-commit      # gitleaks
├── docs/                     # plans, e.g. stage0-plan.md
├── engine/
│   ├── capture/              # WGC, DXGI adapters
│   ├── detect/               # ocr, rules, registry matcher, ui detector
│   ├── fusion/               # mask fusion, tracking, change detection
│   ├── gate/                 # frame states, deadlines, fail-closed logic
│   ├── output/               # protected window, virtual cam
│   ├── registry/             # encrypted secret store
│   ├── policy/               # private apps, config
│   └── report/               # leak report, audit log
├── bench/                    # VEIL-Bench: scenarios, recorder, scorer, fault injection
├── ml/                       # synthetic data, OCR-corruption model, matcher training
├── extensions/
│   ├── chrome/               # Stage 0 hook experiment, later DOM hints
│   └── vscode/               # Stage 5
├── ui/                       # control UI
├── experiments/              # dated experiment logs + results
└── tests/
```

### 12.1 Dev environment

- Python venv at `.venv` (Python 3.13). Recreate with `py -3.13 -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements-dev.txt`.
- Run: `.venv\Scripts\ruff check .`, `.venv\Scripts\ruff format .`, `.venv\Scripts\pytest`.
- gitleaks v8.21.2, built from source with `go install` into `D:\tools\bin` (winget needed a terms prompt; v8.22+ needs Go ≥ 1.24, installed Go is 1.23.2). `D:\tools\bin` must be on PATH. The hook is wired with `git config core.hooksPath .githooks`; re-run that after a fresh clone. If gitleaks isn't found, the hook blocks the commit.
- **C: is nearly full.** Keep caches, models, datasets, and recordings on D:. For Go builds set `GOCACHE`, `GOTMPDIR`, `GOMODCACHE`, `TMP` to `D:\tools\...`.
- GPU: driver 581.83 (supports up to CUDA 13.0). No CUDA toolkit installed (`nvcc` absent); `onnxruntime-gpu` brings its own CUDA runtime libs via pip, so the toolkit should not be needed. Unverified until Stage 2.

---

## 13. Build stages & acceptance criteria

Each stage ends with a pass/fail check. Don't start the next stage until the current one passes (Stage 0 can run in parallel with Stage 1).

**Stage 0 — Browser hook spike (go/no-go).**
Intercept `getDisplayMedia` on a local test page, replace frames with visibly modified frames, confirm the page receives only the modified track. Repeat in a controlled Google Meet call. Test startup, errors thrown inside the wrapper, stop/restart share, reconnection.
✅ Pass: zero unmodified frames observed by the receiver in all cases. Otherwise: browser route shelved; protected window remains the only route. Runs separately and never blocks Stages 1–2; see [docs/stage0-plan.md](docs/stage0-plan.md).

**Stage 1 — VEIL-Bench v0.**
Scenario generator with planted secrets + output recorder + scorer. Validate on "no protection" (should detect leaks) and on a trivial "black everything" output (should detect zero leaks, 100% false-mask).
✅ Pass: scorer correctly identifies planted secrets in raw recordings.

**Stage 2 — Protected-window pipeline.**
Capture one window → OCR + rules → opaque masks → gate → protected window. Share it in Meet.
✅ Pass (the first real demo): a real planted secret appears on screen, the protected output masks it before transmission, and the recorded outgoing frames show it was not readable in that test. Unprocessed frames transmitted = 0, including under fault injection.

**Stage 3 — Rehearsal mode.**
Offline analysis of a recording with a timestamped risk report.
✅ Pass: finds all planted secrets in benchmark recordings; report is readable by a non-technical user.

**Stage 4 — Registry + matcher ablation.**
Encrypted registry, four matchers (§7.2), results on VEIL-Bench.
✅ Pass: written experiment report with leak rate, false-mask rate, and latency per matcher.

**Stage 5 — One source-hint integration (VS Code).**
Extension reports secret positions; engine maps them to captured pixels and fuses with L2.
✅ Pass: measurable improvement on VS Code scenarios vs. L2 alone.

**Stage 6 — Leak report, private apps, further integrations** (browser DOM hints, virtual cam, UIA, terminal) — only after the above.

---

## 14. Working agreement (for AI assistants on this project)

- Read this file first. Check §3 and §4 before proposing any design.
- Work stage by stage. Don't scaffold future stages "just in case".
- Small, testable steps. Every feature comes with a test or a benchmark scenario.
- Never weaken the gate for performance. If it's too slow, lower fps or resolution — never let raw frames through.
- Don't state performance numbers that weren't measured on the RTX 2050.
- Never claim real-time protection is validated before the outgoing stream has been tested (principle 9).
- Log experiments in `experiments/` with date, setup, results, conclusion.
- **All test secrets are synthetic.** No real secrets in the repo, tests, fixtures, logs, datasets, or recordings. Generate fakes at runtime or use obviously fake values. The gitleaks hook is a backstop, not a licence.
- Flag uncertainty honestly; say when something is unverified (e.g. browser hook behavior).
- Keep this file updated when decisions change (see §16).
- Shiven prefers direct, honest, critical feedback over validation.

### 14.1 Who writes what

- **Shiven writes the core**: the frame gate (`engine/gate/`), frame states, and the deadline/fail-closed logic. The assistant explains the design, writes the tests the gate must pass, and reviews Shiven's implementation. It does not write gate implementation code, even when asked "just fix it": it points at the problem and explains instead.
- **The assistant may write non-core code**: setup, fixtures, bench scripts, scenario generators, tooling.
- When ownership of some other part is unclear, ask before writing it.

### 14.2 Code style

The code should read like one careful person wrote it.

- Read the surrounding code first and match it: naming, structure, comment density.
- Comments explain *why* (a constraint, a measured number, a non-obvious failure case), never *what* the next line does.
- Docstrings only where the name and signature don't already say it. No docstring on every function.
- Catch specific exceptions you can handle. No blanket `except Exception` — except at the gate's error exit, where catching everything and blacking out is the point; comment it there.
- No speculative abstractions: no base class or interface with one implementation, no factory for one product, no config for a value that never changes.
- Type hints on function signatures; not on every local.
- Domain names (`frame`, `tile`, `mask`, `deadline`, `approved`), not generic ones (`data`, `handler`, `manager`, `utils`).
- No emoji, banner comments, or decorative output in code or logs.
- Small diffs, one concern per commit. `ruff check` and `ruff format` clean before committing.

---

## 15. Known limitations (state these openly)

- Unknown secret formats in apps that give no source hints can be missed.
- OCR misses on tiny, stylized, or low-contrast text.
- The browser route depends on current Chrome/Meet behavior and may break.
- Gated processing lowers outgoing frame rate and adds latency.
- Windows only.

---

## 16. Open decisions / log

| Date | Decision / question | Status |
|---|---|---|
| 2026-10-29 | Windows desktop protected-window is the primary route | Decided |
| 2026-10-29 | Encrypted registry (DPAPI), no n-gram hashes | Decided |
| 2026-10-29 | Browser hook depends on Stage 0 | Open |
| 2026-10-29 | Python 3.13 venv; ruff + pytest only until a stage needs more | Decided |
| 2026-10-29 | gitleaks v8.21.2 pre-commit hook via `core.hooksPath` | Decided |
| 2026-10-29 | Shiven writes the gate; assistant writes tests and reviews | Decided |
| 2026-10-29 | VEIL-Bench scores with pixel ground truth first, OCR second | Decided |
| 2026-10-29 | Dirty tiles expanded to full text lines before OCR | Decided |
| — | RapidOCR vs PaddleOCR | Open — benchmark in Stage 2 |
| — | Freeze vs blackout on deadline miss (default) | Open — evaluate with users |
| — | Initial per-frame deadline value | Open — measure |

---

## 17. Resume framing (target, fill in real numbers later)

> Built **VEIL**, a local-first screen-sharing privacy engine with fail-closed frame delivery, combining OCR, source-aware detection, and OCR-robust registered-secret matching. Created **VEIL-Bench** to measure secret leakage, false masking, and real-time latency; reduced leaked-frame rate from X% to Y% vs. an OCR+regex baseline at Z fps on an RTX 2050.
