"""Stage 2 pipeline: capture one window -> OCR + rules -> gate -> protected window.

Run: .venv\\Scripts\\python -m engine --window "part of the window title"
Then share the "VEIL protected view" window in the meeting.
"""

import argparse
import itertools
import sys
import threading
import time

from PySide6.QtWidgets import QApplication

from engine.capture.window import WindowSource
from engine.detect.ocr import ScreenDetector, rapidocr
from engine.gate import Gate, OutputKind
from engine.output.window import TITLE, ProtectedWindow

DEADLINE_S = 0.5  # initial target (CLAUDE.md §5.2); scrolls miss it and freeze
RETRY_S = 0.2  # no new frame for this long: the screen has settled

# --faults: a fixed schedule over analysed frames, not random draws, so every
# fault fires in every run (random 1% hangs skipped whole runs). Repeats every
# 30 frames; the hang (longer than the gate's 5 s watchdog) every 100.
FAULT_SCHEDULE = {5: "crash", 12: "garbage box", 18: "slow 0.8 s", 19: "slow 0.8 s"}
HANG_EVERY, HANG_AT = 100, 25


def with_faults(detect):
    """Live fault injection (CLAUDE.md §9.1): every path must still send only safe frames."""
    analysed = itertools.count()

    def faulty(frame):
        n = next(analysed)
        fault = "hang 7 s" if n % HANG_EVERY == HANG_AT else FAULT_SCHEDULE.get(n % 30)
        if fault:
            print(f"{time.strftime('%H:%M:%S')} injected fault: {fault}", file=sys.stderr, flush=True)
        if fault == "crash":
            raise RuntimeError("injected crash")
        if fault == "garbage box":
            return [(50, 50, 10, 10)]  # runs backwards: the gate must treat it as an error
        if fault:
            time.sleep(7 if fault.startswith("hang") else 0.8)
        return detect(frame)

    return faulty


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m engine")
    parser.add_argument("--window", required=True, help="substring of the title of the window to protect")
    parser.add_argument("--cpu", action="store_true", help="run OCR on the CPU (too slow for live use)")
    parser.add_argument("--faults", action="store_true", help="inject crashes, garbage, slowness and hangs")
    args = parser.parse_args()
    if args.window.lower() in TITLE.lower():
        sys.exit("refusing to capture VEIL's own window: the output would feed back into itself")

    app = QApplication(sys.argv)
    ocr = rapidocr({"EngineConfig.onnxruntime.use_cuda": not args.cpu})
    source = WindowSource(args.window)
    state: dict[str, Gate | None] = {"gate": None}

    def run_engine() -> None:
        last = None
        while not source.closed:
            raw = source.next_frame(timeout=RETRY_S)
            gate = state["gate"]
            if raw is None:
                # screen unchanged since the last frame; if that one was late or
                # failed, retry it, or the output stays frozen until the next change
                if last is None or gate is None or gate.output.kind is OutputKind.APPROVED:
                    continue
                raw = source.repeat(last)
            last = raw
            h, w = raw.pixels.shape[:2]
            if gate is None or gate.output.pixels.shape[:2] != (h, w):
                # source window resized: a fresh gate starts blacked out and
                # re-reads everything, rather than masking with stale geometry
                detect = ScreenDetector(ocr)
                gate = state["gate"] = Gate(
                    with_faults(detect) if args.faults else detect, size=(w, h), deadline_s=DEADLINE_S
                )
            gate.submit(raw)

    engine = threading.Thread(target=run_engine, daemon=True)
    engine.start()

    def publish():
        gate = state["gate"]
        if gate is None or source.closed or not engine.is_alive():
            return None  # not started, source gone, or engine died: black
        return gate.publish()

    window = ProtectedWindow(publish)
    window.show()
    try:
        app.exec()
    finally:
        source.stop()


if __name__ == "__main__":
    main()
