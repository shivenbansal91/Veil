"""Are planted (fake) secrets readable in a recording of what a meeting received?

OCR-based (CLAUDE.md §9.1 secondary check): a receiver's recording is scaled and
compressed, so pixel ground truth doesn't apply. Run it on a positive control
first (the unprotected source shared directly): if the secrets aren't readable
there, a clean result on the protected recording means nothing.

Run: .venv\\Scripts\\python -m bench.check_recording RECORDING (--seed N | --secrets FILE)
RECORDING is a video file or a folder of PNGs. --seed matches the planted values
of the experiments' test source; --secrets is a file with one value per line.
"""

import argparse
import difflib
import random
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np

from bench.scenario import _fake_values

READABLE_CHARS = 10  # this many consecutive characters of a planted value count as readable
CHANGE_LEVEL = 12  # grey levels in a 1/8-scale block: above compression noise, below any text change


def changed_frames(path: Path) -> Iterator[tuple[float, np.ndarray]]:
    """(seconds, BGR frame) for every frame that differs locally from the last one kept.

    Local, not mean, difference: a secret flashing up in one line for one frame
    barely moves the frame's mean, and must not be skipped.
    """
    if path.is_dir():
        for i, png in enumerate(sorted(path.glob("*.png"))):
            yield float(i), cv2.imread(str(png))
        return
    video = cv2.VideoCapture(str(path))
    fps = video.get(cv2.CAP_PROP_FPS) or 30.0
    kept, index = None, 0
    while True:
        ok, frame = video.read()
        if not ok:
            break
        small = cv2.resize(
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None, fx=1 / 8, fy=1 / 8, interpolation=cv2.INTER_AREA
        )
        if kept is None or np.abs(small.astype(int) - kept).max() > CHANGE_LEVEL:
            kept = small.astype(int)
            yield index / fps, frame
        index += 1


def readable_secrets(text: str, planted: dict[str, str]) -> dict[str, str]:
    found = {}
    for kind, value in planted.items():
        m = difflib.SequenceMatcher(None, text, value, autojunk=False).find_longest_match()
        if m.size >= READABLE_CHARS:
            found[kind] = value[m.b : m.b + m.size]
    return found


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m bench.check_recording")
    parser.add_argument("recording", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--seed", type=int, help="seed of the test source window (editor session)")
    source.add_argument("--secrets", type=Path, help="file with one planted value per line")
    args = parser.parse_args()

    if args.secrets:
        lines = args.secrets.read_text().split()
        planted = {f"line {i + 1}": v for i, v in enumerate(lines)}
    else:
        values = _fake_values(random.Random(args.seed))
        planted = {
            k: values[k] for k in ("github_pat", "aws_access_key", "stripe_live", "password", "slack_bot")
        }

    import onnxruntime
    from rapidocr import RapidOCR

    onnxruntime.preload_dlls()
    # RapidOCR defaults (including upscaling small text), not the engine's
    # settings: received video is often lower resolution than the screen
    ocr = RapidOCR(params={"EngineConfig.onnxruntime.use_cuda": True, "Global.log_level": "error"})

    kept = black = 0
    seen: dict[str, str] = {}
    for seconds, frame in changed_frames(args.recording):
        kept += 1
        if frame.mean() < 8:
            black += 1
            continue
        found = readable_secrets("\n".join(ocr(frame).txts or ()), planted)
        for kind, fragment in found.items():
            print(f"{seconds:8.2f}s readable {kind}: {fragment}")
        seen |= found

    print(f"{kept} distinct frames checked, {black} black")
    print(f"planted values readable at least once: {len(seen)}/{len(planted)} {sorted(seen)}")


if __name__ == "__main__":
    main()
