"""Fail-closed frame gate. Contract and reasoning: docs/gate-design.md."""

import math
import numbers
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

Box = tuple[float, float, float, float]  # half-open: x0 <= x < x1, y0 <= y < y1


class FrameState(Enum):
    CAPTURED = auto()
    ANALYZING = auto()
    MASKED = auto()
    APPROVED = auto()
    PUBLISHED = auto()
    DEADLINE_MISSED = auto()
    ERROR = auto()


class OutputKind(Enum):
    APPROVED = auto()
    FREEZE = auto()
    BLACKOUT = auto()


@dataclass(frozen=True)
class RawFrame:
    id: int
    captured_at: float  # gate clock reading at capture, seconds
    pixels: np.ndarray  # H x W x 3 uint8


@dataclass(frozen=True)
class OutgoingFrame:
    """The only frame type an output adapter accepts.

    pixels is read-only and never shares memory with a RawFrame.
    """

    kind: OutputKind
    source_id: int | None  # id of the approved frame shown; None for blackout
    pixels: np.ndarray


class Gate:
    def __init__(
        self,
        detect: Callable[[RawFrame], Iterable[Box]],
        *,
        size: tuple[int, int],
        deadline_s: float,
        clock: Callable[[], float] = time.monotonic,
        on_miss: OutputKind = OutputKind.FREEZE,
        hang_s: float = 5.0,
    ):
        if on_miss not in (OutputKind.FREEZE, OutputKind.BLACKOUT):
            raise ValueError(f"on_miss must be FREEZE or BLACKOUT, not {on_miss}")
        self._detect = detect
        self._hang_s = hang_s
        self._busy_since: float | None = None  # clock reading when the in-flight submit() began
        self._width, self._height = size
        self._deadline_s = deadline_s
        self._clock = clock
        self._on_miss = on_miss

        black = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        black.flags.writeable = False
        self._blackout = OutgoingFrame(OutputKind.BLACKOUT, None, black)
        self._last_approved: OutgoingFrame | None = None
        # ponytail: one small list per frame for the whole session (~36k/hour at
        # 10 fps). Fine for v0; move to the audit log when the leak report lands.
        self._states: dict[int, list[FrameState]] = {}
        self._output = self._blackout

    @property
    def output(self) -> OutgoingFrame:
        """What the output adapter shows now. Read-only: only submit() changes it."""
        return self._output

    def states(self, frame_id: int) -> list[FrameState]:
        return list(self._states[frame_id])

    def publish(self) -> OutgoingFrame:
        """Called by the output adapter for the frame it is about to show.

        Safe to call from the output thread while submit() runs on another:
        _output is read once, and it only ever points at a finished safe frame.
        A submit() stuck longer than hang_s (a hung detector never returns, so
        submit() can't time itself out) turns the freeze into blackout.
        """
        busy_since = self._busy_since
        if busy_since is not None and self._clock() - busy_since > self._hang_s:
            return self._blackout
        frame = self._output
        if frame.kind is OutputKind.APPROVED:
            states = self._states[frame.source_id]
            if states[-1] is FrameState.APPROVED:
                states.append(FrameState.PUBLISHED)
        return frame

    def submit(self, raw: RawFrame) -> OutgoingFrame:
        self._busy_since = self._clock()
        try:
            return self._submit(raw)
        finally:
            self._busy_since = None

    def _submit(self, raw: RawFrame) -> OutgoingFrame:
        states = self._states[raw.id] = [FrameState.CAPTURED, FrameState.ANALYZING]
        try:
            masked = self._masked_copy(raw)
        # Deliberately broad: whatever broke in detection or masking, the answer
        # is the same, show nothing. Narrowing this is how a gate fails open.
        except Exception:  # noqa: BLE001
            states.append(FrameState.ERROR)
            return self._show(self._blackout)
        states.append(FrameState.MASKED)

        if self._clock() - raw.captured_at > self._deadline_s:
            states.append(FrameState.DEADLINE_MISSED)
            last = self._last_approved
            if self._on_miss is OutputKind.FREEZE and last is not None:
                return self._show(OutgoingFrame(OutputKind.FREEZE, last.source_id, last.pixels))
            return self._show(self._blackout)

        masked.flags.writeable = False
        states.append(FrameState.APPROVED)
        # the one place an APPROVED frame is made, right after masking
        self._last_approved = OutgoingFrame(OutputKind.APPROVED, raw.id, masked)
        return self._show(self._last_approved)

    def _show(self, frame: OutgoingFrame) -> OutgoingFrame:
        self._output = frame
        return frame

    def _masked_copy(self, raw: RawFrame) -> np.ndarray:
        expected = (self._height, self._width, 3)
        if raw.pixels.shape != expected or raw.pixels.dtype != np.uint8:
            raise ValueError(
                f"frame {raw.id} is {raw.pixels.shape} {raw.pixels.dtype}, gate expects {expected} uint8"
            )
        # Copy before detection, and detect on the copy: capture may reuse its
        # buffer mid-detection, so the live buffer is not what will be sent.
        out = raw.pixels.copy()
        analysed = out.view()
        analysed.flags.writeable = False  # detectors must not draw on the output
        for box in self._detect(RawFrame(raw.id, raw.captured_at, analysed)):
            x0, y0, x1, y1 = _pixel_box(box, self._width, self._height)
            out[y0:y1, x0:x1] = 0
        return out


def _pixel_box(box: Iterable[float], width: int, height: int) -> tuple[int, int, int, int]:
    """Round a detector box outward to whole pixels and clip it to the frame."""
    values = tuple(box)
    if len(values) != 4 or not all(_is_finite_number(v) for v in values):
        raise ValueError(f"malformed box {values!r}")
    x0, y0, x1, y1 = values
    if x1 < x0 or y1 < y0:
        raise ValueError(f"box runs backwards {values!r}")
    # outward: when a detector is unsure about a pixel, it gets masked
    return (
        min(max(math.floor(x0), 0), width),
        min(max(math.floor(y0), 0), height),
        min(max(math.ceil(x1), 0), width),
        min(max(math.ceil(y1), 0), height),
    )


def _is_finite_number(v: object) -> bool:
    return isinstance(v, numbers.Real) and not isinstance(v, bool) and math.isfinite(v)
