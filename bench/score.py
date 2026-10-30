"""Pixel-based leak scoring against scenario ground truth (CLAUDE.md §9.1).

A secret leaks in an output frame if enough of its ink pixels survive unchanged.
Two checks per output frame k:

- Secrets on screen at k: are their ink pixels still in the output?
- Secrets from any other frame (a frozen output may show old content): are their
  ink pixels in the output *where the output differs from screen k*? Pixels equal
  to screen k are explained by what is legitimately on screen now; without this,
  same-coloured text that scrolled into a secret's old spot counts as a leak.
"""

from dataclasses import dataclass

import numpy as np

from bench.scenario import Frame, Rect

# Share of a secret's ink pixels that must survive for it to count as leaked.
# Above 0 because unrelated text drawn at the same spot shares a few
# anti-aliased pixel values by coincidence; low enough that a half-masked token
# (about half its characters readable) is still a leak. Tested both ways.
LEAK_INK_FRACTION = 0.05


@dataclass
class Score:
    leaks: list[set[str]]  # per output frame: kinds of secret visible
    false_mask: list[float]  # per output frame: share of non-secret pixels changed
    secret_frames: int  # input frames containing at least one secret

    @property
    def frames(self) -> int:
        return len(self.leaks)

    @property
    def leaked_frames(self) -> int:
        return sum(bool(kinds) for kinds in self.leaks)

    @property
    def leaked_frame_rate(self) -> float:
        return self.leaked_frames / self.frames

    @property
    def mean_false_mask(self) -> float:
        return sum(self.false_mask) / self.frames


def surviving_ink(
    raw_region: np.ndarray, out_region: np.ndarray, ink: np.ndarray, unexplained: np.ndarray | None = None
) -> float:
    """Share of the secret's ink pixels that are identical in the output,
    counting only pixels in `unexplained` when given."""
    if not ink.any():
        return 0.0
    same = np.all(raw_region == out_region, axis=-1)
    if unexplained is not None:
        same &= unexplained
    return float((ink & same).sum() / ink.sum())


def score(session: list[Frame], outputs: list[np.ndarray]) -> Score:
    """Score output frames; outputs[k] is what was transmitted while session[k] was on screen."""
    occurrences: dict[tuple[str, Rect], tuple[np.ndarray, np.ndarray]] = {}
    for frame in session:
        for s in frame.secrets:
            seen = occurrences.get((s.kind, s.rect))
            # keep the most visible copy: the same spot may be half under a toast in one frame
            if seen is None or s.ink.sum() > seen[1].sum():
                occurrences[s.kind, s.rect] = (frame.pixels[_slice(s.rect)], s.ink)

    leaks, false_mask = [], []
    for raw, out in zip(session, outputs, strict=True):
        on_screen = {(s.kind, s.rect) for s in raw.secrets}
        leaked = {
            s.kind
            for s in raw.secrets
            if surviving_ink(raw.pixels[_slice(s.rect)], out[_slice(s.rect)], s.ink) > LEAK_INK_FRACTION
        }
        unexplained = np.any(out != raw.pixels, axis=-1)
        leaked |= {
            kind
            for (kind, rect), (region, ink) in occurrences.items()
            if (kind, rect) not in on_screen
            and surviving_ink(region, out[_slice(rect)], ink, unexplained[_slice(rect)]) > LEAK_INK_FRACTION
        }
        leaks.append(leaked)
        outside = np.ones(raw.pixels.shape[:2], dtype=bool)
        for s in raw.secrets:
            outside[_slice(s.rect)] = False
        false_mask.append(float((unexplained & outside).sum() / outside.sum()))

    return Score(leaks, false_mask, sum(bool(f.secrets) for f in session))


def _slice(rect: Rect) -> tuple[slice, slice]:
    return slice(rect.y0, rect.y1), slice(rect.x0, rect.x1)


# Baseline outputs. Bench keeps its own instead of importing the engine's masking:
# the thing being measured must not also be the ruler.


def no_protection(frame: Frame) -> np.ndarray:
    return frame.pixels


def black_everything(frame: Frame) -> np.ndarray:
    return np.zeros_like(frame.pixels)


def oracle_mask(frame: Frame) -> np.ndarray:
    """Perfect detection: masks exactly the ground-truth rectangles."""
    out = frame.pixels.copy()
    for s in frame.secrets:
        out[_slice(s.rect)] = 0
    return out
