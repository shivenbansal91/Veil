"""Contract tests for the frame gate (docs/gate-design.md)."""

import math
import random
import threading

import numpy as np
import pytest

from bench.scenario import editor_session
from bench.score import oracle_mask, score
from engine.gate import FrameState, Gate, OutputKind, RawFrame

W, H = 64, 48
DEADLINE = 0.5


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def raw(frame_id, clock, pixels=None):
    if pixels is None:
        pixels = np.random.default_rng(frame_id).integers(1, 256, (H, W, 3), dtype=np.uint8)
    return RawFrame(frame_id, clock.now, pixels)


def make_gate(detect, clock, **kw):
    return Gate(detect, size=(W, H), deadline_s=DEADLINE, clock=clock, **kw)


def boxes(*rects):
    return lambda frame: list(rects)


def fails(frame):
    raise RuntimeError("detector crashed")


def is_black(out):
    return out.pixels.shape == (H, W, 3) and not out.pixels.any()


def test_starts_blacked_out():
    gate = make_gate(boxes(), FakeClock())
    assert gate.output.kind == OutputKind.BLACKOUT
    assert gate.output.source_id is None
    assert is_black(gate.output)


def test_approved_frame_masks_exactly_the_boxes():
    clock = FakeClock()
    gate = make_gate(boxes((10, 5, 20, 15), (40, 30, 50, 40)), clock)
    frame = raw(1, clock)
    out = gate.submit(frame)

    assert out.kind == OutputKind.APPROVED and out.source_id == 1
    assert gate.output is out
    expected = frame.pixels.copy()
    expected[5:15, 10:20] = 0
    expected[30:40, 40:50] = 0
    assert np.array_equal(out.pixels, expected)


def test_output_never_shares_memory_with_raw():
    clock = FakeClock()
    gate = make_gate(boxes((0, 0, 8, 8)), clock)
    frame = raw(1, clock)
    out = gate.submit(frame)
    assert not np.shares_memory(out.pixels, frame.pixels)

    before = out.pixels.copy()
    frame.pixels[:] = 255  # capture reusing its buffer must not reach the output
    assert np.array_equal(out.pixels, before)


def test_output_never_shares_memory_with_raw_even_with_nothing_to_mask():
    clock = FakeClock()
    frame = raw(1, clock)
    out = make_gate(boxes(), clock).submit(frame)
    assert out.kind == OutputKind.APPROVED
    assert np.array_equal(out.pixels, frame.pixels)
    assert not np.shares_memory(out.pixels, frame.pixels)


def test_output_pixels_are_read_only():
    clock = FakeClock()
    gate = make_gate(boxes(), clock)
    for out in (gate.output, gate.submit(raw(1, clock))):
        with pytest.raises(ValueError):
            out.pixels[0, 0] = 1


def test_output_cannot_be_replaced_from_outside():
    clock = FakeClock()
    gate = make_gate(boxes(), clock)
    frame = raw(1, clock)
    with pytest.raises(AttributeError):
        gate.output = frame  # e.g. a capture path "helpfully" showing the raw frame
    assert gate.output.kind == OutputKind.BLACKOUT


def test_detector_error_blacks_out_then_recovers():
    clock = FakeClock()
    detect = {"current": boxes()}
    gate = make_gate(lambda f: detect["current"](f), clock)
    gate.submit(raw(1, clock))

    detect["current"] = fails
    out = gate.submit(raw(2, clock))
    assert out.kind == OutputKind.BLACKOUT and is_black(out)

    detect["current"] = boxes()
    assert gate.submit(raw(3, clock)).kind == OutputKind.APPROVED


def test_detector_error_while_iterating_boxes_blacks_out():
    clock = FakeClock()

    def half_then_crash(frame):
        yield (0, 0, 4, 4)
        raise ValueError("ocr fell over mid-way")

    out = make_gate(half_then_crash, clock).submit(raw(1, clock))
    assert out.kind == OutputKind.BLACKOUT and is_black(out)


def slow(seconds, clock, rects=()):
    def detect(frame):
        clock.now += seconds
        return list(rects)

    return detect


def test_deadline_miss_freezes_on_last_approved():
    clock = FakeClock()
    detect = {"current": boxes((0, 0, 5, 5))}
    gate = make_gate(lambda f: detect["current"](f), clock)
    approved = gate.submit(raw(1, clock))

    detect["current"] = slow(DEADLINE + 0.01, clock)
    late = raw(2, clock)
    out = gate.submit(late)
    assert out.kind == OutputKind.FREEZE
    assert out.source_id == 1
    assert np.array_equal(out.pixels, approved.pixels)
    assert not np.array_equal(out.pixels, late.pixels)


def test_deadline_counts_from_capture_not_from_submit():
    clock = FakeClock()
    gate = make_gate(boxes(), clock)
    frame = raw(1, clock)  # captured at t=0
    clock.now = DEADLINE + 0.01  # sat in a queue before reaching the gate
    assert gate.submit(frame).kind == OutputKind.BLACKOUT


def test_exactly_on_the_deadline_is_in_time():
    clock = FakeClock()
    out = make_gate(slow(DEADLINE, clock), clock).submit(raw(1, clock))
    assert out.kind == OutputKind.APPROVED


def test_deadline_miss_blacks_out_without_an_approved_frame():
    clock = FakeClock()
    out = make_gate(slow(DEADLINE + 0.01, clock), clock).submit(raw(1, clock))
    assert out.kind == OutputKind.BLACKOUT and is_black(out)


def test_on_miss_blackout_never_freezes():
    clock = FakeClock()
    detect = {"current": boxes()}
    gate = make_gate(lambda f: detect["current"](f), clock, on_miss=OutputKind.BLACKOUT)
    gate.submit(raw(1, clock))
    detect["current"] = slow(DEADLINE + 0.01, clock)
    out = gate.submit(raw(2, clock))
    assert out.kind == OutputKind.BLACKOUT and is_black(out)


def test_boxes_round_outward_and_clip():
    clock = FakeClock()
    frame = raw(1, clock)
    gate = make_gate(
        boxes((10.4, 20.6, 30.2, 40.1), (-5, -5, 3, 3), (W + 10, 0, W + 20, 5), (60, 44, 99, 99)), clock
    )
    out = gate.submit(frame)

    assert out.kind == OutputKind.APPROVED
    expected = frame.pixels.copy()
    expected[20:41, 10:31] = 0  # floor(20.6)=20, ceil(40.1)=41, floor(10.4)=10, ceil(30.2)=31
    expected[0:3, 0:3] = 0
    expected[44:H, 60:W] = 0
    assert np.array_equal(out.pixels, expected)


@pytest.mark.parametrize(
    "bad",
    [(10, 10, 5, 20), (10, 10, 20, 5), (math.nan, 0, 5, 5), (0, 0, 5), ("a", 0, 5, 5), None],
    ids=["x-backwards", "y-backwards", "nan", "three-numbers", "string", "none"],
)
def test_malformed_boxes_are_an_error(bad):
    clock = FakeClock()
    out = make_gate(boxes(bad), clock).submit(raw(1, clock))
    assert out.kind == OutputKind.BLACKOUT and is_black(out)


def test_wrong_frame_size_is_an_error():
    clock = FakeClock()
    bigger = np.full((H + 10, W + 10, 3), 200, dtype=np.uint8)
    out = make_gate(boxes(), clock).submit(raw(1, clock, bigger))
    assert out.kind == OutputKind.BLACKOUT and is_black(out)


def test_states_of_an_approved_frame():
    clock = FakeClock()
    gate = make_gate(boxes((0, 0, 4, 4)), clock)
    gate.submit(raw(7, clock))
    assert gate.states(7) == [
        FrameState.CAPTURED,
        FrameState.ANALYZING,
        FrameState.MASKED,
        FrameState.APPROVED,
    ]


def test_states_of_a_failed_frame():
    clock = FakeClock()
    gate = make_gate(fails, clock)
    gate.submit(raw(7, clock))
    assert gate.states(7) == [FrameState.CAPTURED, FrameState.ANALYZING, FrameState.ERROR]


def test_states_of_a_late_frame():
    clock = FakeClock()
    gate = make_gate(slow(DEADLINE + 0.01, clock), clock)
    gate.submit(raw(7, clock))
    assert gate.states(7) == [
        FrameState.CAPTURED,
        FrameState.ANALYZING,
        FrameState.MASKED,
        FrameState.DEADLINE_MISSED,
    ]


def test_publish_before_any_frame_is_blackout():
    gate = make_gate(boxes(), FakeClock())
    out = gate.publish()
    assert out.kind == OutputKind.BLACKOUT and is_black(out)


def test_publish_marks_an_approved_frame_published_once():
    clock = FakeClock()
    gate = make_gate(boxes(), clock)
    approved = gate.submit(raw(7, clock))
    assert gate.publish() is approved
    gate.publish()  # the output loop re-shows the same frame until a new one arrives
    assert gate.states(7) == [
        FrameState.CAPTURED,
        FrameState.ANALYZING,
        FrameState.MASKED,
        FrameState.APPROVED,
        FrameState.PUBLISHED,
    ]


def test_failed_and_late_frames_are_never_published():
    clock = FakeClock()
    detect = {"current": boxes()}
    gate = make_gate(lambda f: detect["current"](f), clock)
    gate.submit(raw(1, clock))
    gate.publish()

    detect["current"] = fails
    gate.submit(raw(2, clock))
    gate.publish()
    detect["current"] = slow(DEADLINE + 0.01, clock)
    frozen = gate.submit(raw(3, clock))
    assert gate.publish() is frozen and frozen.source_id == 1

    assert FrameState.PUBLISHED not in gate.states(2)
    assert FrameState.PUBLISHED not in gate.states(3)
    assert gate.states(1).count(FrameState.PUBLISHED) == 1


def test_an_approved_frame_replaced_before_publishing_is_never_published():
    clock = FakeClock()
    gate = make_gate(boxes(), clock)
    gate.submit(raw(1, clock))
    gate.submit(raw(2, clock))  # output loop was slower than the gate
    gate.publish()
    assert FrameState.PUBLISHED not in gate.states(1)
    assert gate.states(2)[-1] == FrameState.PUBLISHED


def test_hung_detector_never_exposes_the_frame_in_flight():
    """While a detector hangs on another thread, the output stays on the last
    safe frame. v0 does not switch to blackout during a hang (Stage 2 watchdog)."""
    clock = FakeClock()
    entered, release = threading.Event(), threading.Event()
    detect = {"current": boxes((0, 0, 8, 8))}

    def hang(frame):
        entered.set()
        release.wait(timeout=5)
        return []

    gate = make_gate(lambda f: detect["current"](f), clock)
    approved = gate.submit(raw(1, clock))

    detect["current"] = hang
    in_flight = raw(2, clock)
    worker = threading.Thread(target=gate.submit, args=(in_flight,))
    worker.start()
    assert entered.wait(timeout=5)
    for _ in range(50):
        out = gate.publish()
        assert out is approved
        assert not np.array_equal(out.pixels, in_flight.pixels)
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()


def test_fault_injection_never_transmits_unmasked_pixels():
    """200 frames of the bench scenario; the detector is perfect when it works,
    so any leak the scorer finds is the gate's fault, not detection's."""
    rng = random.Random(1234)
    session = editor_session(seed=11, n_frames=40, size=(640, 360))
    clock = FakeClock()
    mode = {"current": "ok"}

    def detect(frame):
        gt = by_id[frame.id].secrets
        if mode["current"] == "crash":
            raise RuntimeError("injected crash")
        if mode["current"] == "garbage":
            return [(5, 5, 1, 1)]
        if mode["current"] == "slow":
            clock.now += DEADLINE * 2
        return [s.rect for s in gt]

    gate = Gate(detect, size=(640, 360), deadline_s=DEADLINE, clock=clock)
    by_id, shown, outputs, approved, seen = {}, [], [], {}, set()
    for frame_id in range(200):
        bench_frame = session[frame_id % len(session)]
        by_id[frame_id] = bench_frame
        mode["current"] = rng.choices(["ok", "crash", "garbage", "slow"], weights=[5, 1, 1, 2])[0]
        clock.now += 0.1
        gate.submit(RawFrame(frame_id, clock.now, bench_frame.pixels.copy()))
        out = gate.publish()

        if out.kind == OutputKind.APPROVED:
            assert out.source_id == frame_id
            assert np.array_equal(out.pixels, oracle_mask(bench_frame))
            approved[frame_id] = out.pixels.copy()
        elif out.kind == OutputKind.FREEZE:
            assert np.array_equal(out.pixels, approved[out.source_id])
        else:
            assert not out.pixels.any()
        shown.append(bench_frame)
        outputs.append(out.pixels)
        seen.add(out.kind)

    assert seen == {OutputKind.APPROVED, OutputKind.FREEZE, OutputKind.BLACKOUT}  # every path was exercised
    assert score(shown, outputs).leaked_frames == 0
    for frame_id in range(200):
        states = gate.states(frame_id)
        if FrameState.PUBLISHED in states:
            assert states[-2:] == [FrameState.APPROVED, FrameState.PUBLISHED]
