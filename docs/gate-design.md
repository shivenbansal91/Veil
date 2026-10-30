# Frame gate: design and contract (v0)

Implemented in `engine/gate.py`. The tests in `tests/test_gate.py` are the contract: change them and this doc first, then the code (CLAUDE.md §14.1).

## The one property

**No frame reaches the output unless it went through masking.** Detection quality is a separate question (CLAUDE.md §4.1). The gate doesn't care whether the detector found every secret, only that nothing skips the step.

## Decisions, and why

### 1. Two frame types, one direction

```
RawFrame ──► Gate.submit() ──► OutgoingFrame ──► output adapter
```

- `RawFrame(id, captured_at, pixels)`: what capture produces. Only the gate and detectors see it.
- `OutgoingFrame(kind, source_id, pixels)`: the only type an output adapter accepts. `kind` is `APPROVED`, `FREEZE` or `BLACKOUT`.

Why types and not a flag like `frame.approved = True`: a flag is something any code can set or forget to check. With a separate type, "can the output reach raw pixels?" turns into "does `engine/output/` ever import `RawFrame`?", which a reviewer, or later a test, can check.

Honest limit: Python can't make this *impossible*; anyone can construct an `OutgoingFrame` by hand. What we get is that the right thing is the easy path, plus tests that would catch the wrong thing. Rule for review: `OutgoingFrame(kind=APPROVED, …)` is created in exactly one place, right after masking.

### 2. Copy, then mask. Never alias the raw buffer

The approved frame's pixels are a **new array**, never a view of `raw.pixels`, even when there is nothing to mask. And they are **read-only** (`pixels.flags.writeable = False`).

Why: numpy slicing and many "return the input unchanged" shortcuts produce views that share memory. If the output shares memory with a raw buffer, then when capture reuses or overwrites that buffer, raw pixels appear in the output without ever passing through the gate. That's a leak with no bug in the gate's logic, which makes it the worst kind. The tests check `np.shares_memory` and that writing to output pixels raises.

The detector runs on a **read-only view of that copy**, not on `raw.pixels` (fixed 2026-10-30, found while planning capture). Otherwise a capture library that reuses its buffer can overwrite it mid-detection: the detector finds boxes for the new content while the copy, which is what gets sent, still holds the old content and its secrets. Read-only also stops in-place preprocessing in a detector from drawing on the output; such a write raises, which is an error, which is blackout. Tests: `test_detector_analyses_the_pixels_that_are_sent`, `test_detector_cannot_write_into_the_output`.

### 3. Default deny: every non-success path ends at a pre-made safe frame

- Before the first approval: `BLACKOUT`.
- Detector raises, returns garbage, or the frame has the wrong size: `ERROR` → `BLACKOUT`.
- Approved too late: `DEADLINE_MISSED` → `FREEZE` (re-show the last approved frame) or `BLACKOUT` if there isn't one, or if the gate was built with `on_miss=BLACKOUT`.

So the code has exactly one path that produces something derived from the new raw frame, and it goes through masking. Every other branch returns something that already existed. Why freeze on a miss but blackout on an error: a miss means "slow", and the last approved frame is still a fully processed frame. An error means something is broken and we don't know what, so show nothing. Which default is better for a miss is still open (CLAUDE.md §16); `on_miss` lets the bench compare them.

### 4. Deadline is measured end to end

`clock() - raw.captured_at > deadline_s` at approval time means a miss. Measured from **capture**, not from the start of `submit`, because the meeting sees how stale the frame is since it was on screen, not since the gate began working on it. The clock is injected (`clock=`), so tests control time exactly instead of sleeping.

### 5. Rectangles: rounded outward, clipped, strict about garbage

Detectors (OCR) return float boxes. The gate:
- rounds **outward** (floor the start, ceil the end): when in doubt, mask more;
- clips to the frame: a box partly off-screen masks the part that's on-screen; one fully off-screen masks nothing;
- treats malformed boxes as an **error** (end before start, NaN, not four numbers): a detector returning nonsense is a broken detector, so blackout.

Boxes are half-open: `(x0, y0, x1, y1)` covers `x0 <= x < x1`, `y0 <= y < y1`, the same as numpy slicing.

### 6. States are recorded per frame

`gate.states(frame_id)` returns the state sequence, e.g. `[CAPTURED, ANALYZING, MASKED, APPROVED]` or `[CAPTURED, ANALYZING, ERROR]`. That's the audit trail the leak report (Stage 6) is built from, and it lets the tests check that a frame went through each stage, not just that the output looks fine.

### 7. Synchronous for v0, and what that doesn't cover

`submit()` runs detection inline and returns. That makes every test deterministic, which is the right first step. What it can't do:

- **Hung detector:** `submit()` never returns, so it can't time itself out. Readers on another thread keep getting the last safe frame (`test_hung_detector_never_exposes_the_frame_in_flight`), and once a submit has been in flight longer than `hang_s` (default 5 s, above the ~3.7 s worst case measured for full-frame OCR during scrolls), `publish()` returns blackout instead (`test_detector_hung_past_hang_s_blacks_out_then_recovers`). A slow submit that finishes never triggers it (`test_slow_but_finished_submits_never_trigger_the_hang_blackout`). Added 2026-10-30, Stage 2.
- **Process crash:** the protected window lives in the engine's process, so a crash removes the window rather than painting black. What the meeting shows then is untested (needs the Meet test). If only the engine thread dies, the window paints black (`engine/__main__.py`).
- `POLICY_DENY` arrives with private apps in Stage 6.

### 8. PUBLISHED is set by the reader, not the gate

The output adapter calls `gate.publish()` for each frame it shows. That returns `gate.output` and, the first time an approved frame is handed out, appends `PUBLISHED` to its states. So `PUBLISHED` means "actually reached the output", not "was ready". An approved frame replaced by a newer one before the output loop came round is never `PUBLISHED`, and neither is a late or failed frame (a freeze re-shows an *older* frame, which was already published). `publish()` reads `_output` once, and `_output` only ever points at a finished safe frame, so it's safe to call from the output thread while `submit()` runs on another.

## The contract (what the tests import)

```python
from engine.gate import FrameState, Gate, OutgoingFrame, OutputKind, RawFrame

RawFrame(id: int, captured_at: float, pixels: np.ndarray)   # H x W x 3 uint8
Gate(detect, *, size=(width, height), deadline_s, clock=time.monotonic, on_miss=OutputKind.FREEZE, hang_s=5.0)
    detect: Callable[[RawFrame], Iterable[tuple[number, number, number, number]]]
gate.submit(raw) -> OutgoingFrame        # also becomes gate.output
gate.output -> OutgoingFrame             # read-only property: what is shown now
gate.publish() -> OutgoingFrame          # output adapter takes the frame to show; marks PUBLISHED;
                                         # blackout while a submit() is stuck past hang_s
gate.states(frame_id) -> list[FrameState]
OutgoingFrame.kind: OutputKind           # APPROVED | FREEZE | BLACKOUT
OutgoingFrame.source_id: int | None      # id of the approved frame shown; None for blackout
OutgoingFrame.pixels: np.ndarray         # read-only, owns its memory
FrameState: CAPTURED, ANALYZING, MASKED, APPROVED, PUBLISHED, DEADLINE_MISSED, ERROR
```

Mask fill and blackout are both solid black `(0, 0, 0)` for now (opaque, principle 3).

## What the tests prove

| Test | Proves |
|---|---|
| `test_starts_blacked_out` | Nothing is shown before the first approval |
| `test_approved_frame_masks_exactly_the_boxes` | Masking is opaque and touches only the boxes |
| `test_output_never_shares_memory_with_raw` (+ `_even_with_nothing_to_mask`) | Decision 2 |
| `test_output_pixels_are_read_only` | Decision 2 |
| `test_detector_error_blacks_out_then_recovers` | Error → blackout, and the gate keeps working |
| `test_deadline_miss_freezes_on_last_approved` / `_blacks_out_without_one` / `on_miss_blackout` | Decision 3 |
| `test_boxes_round_outward_and_clip` / `test_malformed_boxes_are_an_error` | Decision 5 |
| `test_wrong_frame_size_is_an_error` | A resized source window can't slip through |
| `test_states_*` | Decision 6 |
| `test_publish_*`, `test_failed_and_late_frames_are_never_published`, `test_an_approved_frame_replaced_before_publishing_is_never_published` | Decision 8 |
| `test_output_cannot_be_replaced_from_outside` | Only `submit()` changes what is shown |
| `test_hung_detector_never_exposes_the_frame_in_flight` | A hang on another thread can't leak the frame being analysed |
| `test_fault_injection_never_transmits_unmasked_pixels` | The one property, end to end: 200 frames of the bench scenario with random detector crashes, garbage, and deadline misses; every output frame is checked by pixels against ground truth with the VEIL-Bench scorer |
