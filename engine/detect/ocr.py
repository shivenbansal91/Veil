"""L2 vision detector: OCR on changed bands only, then rules (CLAUDE.md §5.3, §6.1).

A ScreenDetector is the `detect` callable a Gate runs. It is stateful: masks
found in rows that did not change are carried to the next frame unchanged, and
only changed rows are re-read. Rows are compared whole, so every OCR input is
a full-width band and a token is never cut at a tile edge.
"""

from collections.abc import Callable
from typing import NamedTuple

import numpy as np

from engine.detect.rules import find_secrets

Box = tuple[int, int, int, int]  # half-open x0, y0, x1, y1


class Line(NamedTuple):
    text: str
    box: Box
    chars: list[Box] | None  # one box per non-space character, when the OCR provides them


Ocr = Callable[[np.ndarray], list[Line]]  # RGB band -> lines, boxes in band coordinates

BAND_MARGIN = 24  # px above and below a changed row: about one text line, for wrapped secrets


class ScreenDetector:
    def __init__(self, ocr: Ocr):
        self._ocr = ocr
        self._prev: np.ndarray | None = None
        self._masks: list[Box] = []

    def __call__(self, frame) -> list[Box]:
        pixels = frame.pixels
        prev, self._prev = self._prev, None  # if anything below raises, the next frame starts fresh
        height = pixels.shape[0]
        if prev is None or prev.shape != pixels.shape:
            bands, kept = [(0, height)], []
        else:
            changed = np.flatnonzero(np.any(pixels != prev, axis=(1, 2)))
            bands = _grow_over_masks(_bands(changed, height), self._masks)
            kept = [m for m in self._masks if not any(_rows_overlap(m, band) for band in bands)]

        found = []
        for y0, y1 in bands:
            for x0, by0, x1, by1 in secret_boxes(self._ocr(pixels[y0:y1])):
                found.append((x0, by0 + y0, x1, by1 + y0))
        self._masks = kept + found
        self._prev = pixels.copy()  # the gate masks its copy after this returns; keep the unmasked frame
        return self._masks


def secret_boxes(lines: list[Line]) -> list[Box]:
    boxes = []
    for line in _join_rows(lines):
        # several rules often fire on one token (format, KEY= name, entropy): merge them
        spans: list[list[int]] = []
        for m in sorted(find_secrets(line.text)):
            if spans and m.start <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], m.end)
            else:
                spans.append([m.start, m.end])
        boxes += [_span_box(line, start, end) for start, end in spans]
    return boxes


def _join_rows(lines: list[Line]) -> list[Line]:
    """OCR splits a line where its colour changes, so syntax highlighting gives
    `api_key` and `= 'value'` as two pieces and no KEY=value rule sees both.
    Join the pieces that share a row, left to right."""
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda ln: ln.box[0]):
        for row in rows:
            if _same_row(row[-1].box, line.box):
                row.append(line)
                break
        else:
            rows.append([line])
    return [_join(row) for row in rows]


def _same_row(a: Box, b: Box) -> bool:
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap > min(a[3] - a[1], b[3] - b[1]) / 2


def _join(pieces: list[Line]) -> Line:
    text, (x0, y0, x1, y1), chars = pieces[0]
    if chars is not None and len(chars) != sum(not c.isspace() for c in text):
        chars = None
    for piece in pieces[1:]:
        ptext, pchars = piece.text, piece.chars
        nonspace = [i for i, c in enumerate(ptext) if not c.isspace()]
        if chars is not None and pchars is not None and len(pchars) == len(nonspace):
            # overlapping pieces repeat the shared characters (`password` then
            # `d = '...'`): drop the ones whose centre is inside what's joined so far
            k = 0
            while k < len(pchars) and (pchars[k][0] + pchars[k][2]) / 2 < x1:
                k += 1
            if k == len(pchars):
                continue
            ptext, pchars, left = ptext[nonspace[k] :], pchars[k:], pchars[k][0]
        else:
            chars, left = None, piece.box[0]
        char_w = (x1 - x0) / max(len(text), 1)
        text += (" " if left - x1 > char_w / 2 else "") + ptext
        if chars is not None:
            chars = chars + pchars
        x0, y0, x1, y1 = (
            min(x0, piece.box[0]),
            min(y0, piece.box[1]),
            max(x1, piece.box[2]),
            max(y1, piece.box[3]),
        )
    return Line(text, (x0, y0, x1, y1), chars)


def _span_box(line: Line, start: int, end: int) -> Box:
    x0, y0, x1, y1 = line.box
    nonspace = [i for i, c in enumerate(line.text) if not c.isspace()]
    if line.chars is None or len(line.chars) != len(nonspace):
        return line.box  # can't place the span inside the line: mask the whole line
    inside = [line.chars[k] for k, i in enumerate(nonspace) if start <= i < end]
    if not inside:
        return line.box
    # half a character of slack each side: CTC character positions are approximate
    pad = max(2, (x1 - x0) // max(len(line.text), 1) // 2)
    return (
        max(min(b[0] for b in inside) - pad, x0),
        y0,
        min(max(b[2] for b in inside) + pad, x1),
        y1,
    )


def _bands(changed_rows: np.ndarray, height: int) -> list[tuple[int, int]]:
    bands: list[tuple[int, int]] = []
    for row in changed_rows:
        y0, y1 = max(row - BAND_MARGIN, 0), min(row + 1 + BAND_MARGIN, height)
        if bands and y0 <= bands[-1][1]:
            bands[-1] = (bands[-1][0], max(bands[-1][1], y1))
        else:
            bands.append((y0, y1))
    return bands


def _grow_over_masks(bands: list[tuple[int, int]], masks: list[Box]) -> list[tuple[int, int]]:
    """A band touching a mask grows to cover it, so a secret is never half re-read."""
    grown = []
    for y0, y1 in bands:
        for m in masks:
            if _rows_overlap(m, (y0, y1)):
                y0, y1 = min(y0, m[1]), max(y1, m[3])
        if grown and y0 <= grown[-1][1]:
            grown[-1] = (min(grown[-1][0], y0), max(grown[-1][1], y1))
        else:
            grown.append((y0, y1))
    # ponytail: one pass; a band grown into a mask that touches a further mask
    # stops there. Fine while masks are single text lines.
    return grown


def _rows_overlap(box: Box, band: tuple[int, int]) -> bool:
    return box[1] < band[1] and band[0] < box[3]


def rapidocr(params: dict | None = None) -> Ocr:
    """RapidOCR as an Ocr. Settings from experiments/2026-10-30-ocr-benchmark."""
    from rapidocr import RapidOCR

    if (params or {}).get("EngineConfig.onnxruntime.use_cuda"):
        import onnxruntime

        onnxruntime.preload_dlls()  # else the CUDA DLLs from the pip wheels are not found
    base = {
        "Global.use_cls": False,  # screen text is upright
        "Global.return_word_box": True,
        "Global.return_single_char_box": True,
        "Global.log_level": "error",
        "Det.limit_type": "max",  # the default "min" upscales thin bands ~12x
        "Det.limit_side_len": 1920,
    }
    engine = RapidOCR(params=base | (params or {}))

    def ocr(band: np.ndarray) -> list[Line]:
        result = engine(np.ascontiguousarray(band[:, :, ::-1]))  # RapidOCR expects BGR
        if not result.txts:
            return []
        lines = []
        for text, quad, chars in zip(result.txts, result.boxes, result.word_results, strict=True):
            char_boxes = [_quad_box(c[2]) for c in chars] if chars else None
            lines.append(Line(text, _quad_box(quad), char_boxes))
        return lines

    return ocr


def _quad_box(quad) -> Box:
    """RapidOCR gives 4 corner points; screen text is axis-aligned, so take the bounds."""
    pts = np.asarray(quad, dtype=float)
    return (
        int(np.floor(pts[:, 0].min())),
        int(np.floor(pts[:, 1].min())),
        int(np.ceil(pts[:, 0].max())),
        int(np.ceil(pts[:, 1].max())),
    )
