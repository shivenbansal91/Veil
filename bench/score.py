"""Pixel-based leak scoring against scenario ground truth (CLAUDE.md §9.1).

A secret leaks in an output frame if READABLE_RUN consecutive characters of it
survive: a character survives when nearly all its glyph pixels are unchanged.
Two checks per output frame k:

- Secrets on screen at k: are their ink pixels still in the output?
- Secrets from any other frame (a frozen output may show old content): are their
  ink pixels in the output *where the output differs from screen k*? Pixels equal
  to screen k are explained by what is legitimately on screen now; without this,
  same-coloured text that scrolled into a secret's old spot counts as a leak.
"""

from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from bench.scenario import Frame, Rect, Secret

# Counted per character, not per pixel: a frozen frame can show other text in
# the same colour where a secret sits, and ~8% of glyph pixels then match by
# coincidence (found in the terminal scenario). Unrelated text almost never
# reproduces 3 of the secret's glyphs in a row at the same place; a half-masked
# token keeps ~20. Tested both ways, and a 1-2 char sliver doesn't count.
READABLE_RUN = 3
CHAR_SURVIVES = 0.9  # share of a character's glyph pixels that must be unchanged


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


def readable(
    raw_region: np.ndarray,
    out_region: np.ndarray,
    ink: np.ndarray,
    n_chars: int,
    unexplained: np.ndarray | None = None,
) -> bool:
    """READABLE_RUN consecutive characters survive in the output, or all of them
    for a part shorter than that (a wrapped token can leave 2 chars on a row).
    Counts only pixels in `unexplained` when given. Cells are equal slices of
    the box, which matches a monospaced font."""
    same = np.all(raw_region == out_region, axis=-1)
    if unexplained is not None:
        same &= unexplained
    edges = np.linspace(0, ink.shape[1], n_chars + 1).round().astype(int)
    best = run = visible = 0
    for a, b in pairwise(edges):
        glyph = ink[:, a:b]
        if not glyph.any():
            continue  # hidden (e.g. under a toast) or a space: neither breaks nor extends a run
        visible += 1
        if (glyph & same[:, a:b]).sum() >= CHAR_SURVIVES * glyph.sum():
            run += 1
            best = max(best, run)
        else:
            run = 0
    return visible > 0 and best >= min(READABLE_RUN, visible)


def score(session: list[Frame], outputs: list[np.ndarray]) -> Score:
    """Score output frames; outputs[k] is what was transmitted while session[k] was on screen."""
    occurrences: dict[tuple[str, Rect], tuple[np.ndarray, Secret]] = {}
    for frame in session:
        for s in frame.secrets:
            seen = occurrences.get((s.kind, s.rect))
            # keep the most visible copy: the same spot may be half under a toast in one frame
            if seen is None or s.ink.sum() > seen[1].ink.sum():
                occurrences[s.kind, s.rect] = (frame.pixels[_slice(s.rect)], s)

    leaks, false_mask = [], []
    for raw, out in zip(session, outputs, strict=True):
        on_screen = {(s.kind, s.rect) for s in raw.secrets}
        leaked = {
            s.kind
            for s in raw.secrets
            if readable(raw.pixels[_slice(s.rect)], out[_slice(s.rect)], s.ink, len(s.value))
        }
        unexplained = np.any(out != raw.pixels, axis=-1)
        leaked |= {
            kind
            for (kind, rect), (region, past) in occurrences.items()
            if (kind, rect) not in on_screen
            and readable(region, out[_slice(rect)], past.ink, len(past.value), unexplained[_slice(rect)])
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
