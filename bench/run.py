"""Run a pipeline over a session: what it leaks (score.py) and what it costs.

A pipeline maps one input frame to the pixels transmitted for it. Latency is
per call, so throughput here is what one sequential pipeline sustains.

Run: .venv\\Scripts\\python -m bench.run
"""

import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from bench.scenario import Frame, editor_session, terminal_session
from bench.score import Score, black_everything, no_protection, oracle_mask, score

Pipeline = Callable[[Frame], np.ndarray]


@dataclass
class BenchResult:
    score: Score
    latencies_ms: list[float]

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms)

    @property
    def p95_ms(self) -> float:
        return statistics.quantiles(self.latencies_ms, n=20)[18]

    @property
    def fps(self) -> float:
        return 1000 * len(self.latencies_ms) / sum(self.latencies_ms)


def run(session: list[Frame], pipeline: Pipeline) -> BenchResult:
    outputs, latencies = [], []
    for frame in session:
        t = time.perf_counter()
        outputs.append(pipeline(frame))
        latencies.append((time.perf_counter() - t) * 1000)
    return BenchResult(score(session, outputs), latencies)


def gate_pipeline(
    detect: Callable[[Frame], list], size: tuple[int, int], deadline_s: float = 0.5
) -> Pipeline:
    """Wrap the engine's gate as a pipeline. The bench only measures it; the
    scoring above never imports engine code."""
    from engine.gate import Gate, RawFrame

    gate = Gate(lambda raw: detect(frames[raw.id]), size=size, deadline_s=deadline_s)
    frames: dict[int, Frame] = {}

    def pipeline(frame: Frame) -> np.ndarray:
        frame_id = len(frames)
        frames[frame_id] = frame
        gate.submit(RawFrame(frame_id, time.monotonic(), frame.pixels))
        return gate.publish().pixels

    return pipeline


def main() -> None:
    with_ocr = "--ocr" in sys.argv  # slow, needs the GPU: off by default
    if with_ocr:
        from engine.detect.ocr import ScreenDetector, rapidocr

        ocr = rapidocr({"EngineConfig.onnxruntime.use_cuda": True})
    sessions = {
        "editor": editor_session(seed=1, n_frames=30, size=(1920, 1080)),
        "terminal": terminal_session(seed=1, n_frames=30, size=(1920, 1080)),
    }
    print(
        f"{'session':<9} {'pipeline':<30} {'leaked':>8} {'false-mask':>10} {'p50 ms':>8} {'p95 ms':>8} {'fps':>8}"
    )
    for name, session in sessions.items():
        size = session[0].pixels.shape[1::-1]
        pipelines = {
            "no protection": no_protection,
            "black everything": black_everything,
            "oracle mask": oracle_mask,
            "gate + oracle detector": gate_pipeline(lambda f: [s.rect for s in f.secrets], size),
        }
        if with_ocr:
            # no deadline: what detection alone misses; 500 ms: what the live gate would send
            pipelines["gate + OCR/rules, no deadline"] = gate_pipeline(ScreenDetector(ocr), size, 1e9)
            pipelines["gate + OCR/rules, 500 ms"] = gate_pipeline(ScreenDetector(ocr), size, 0.5)
        for label, pipeline in pipelines.items():
            r = run(session, pipeline)
            s = r.score
            print(
                f"{name:<9} {label:<30} {s.leaked_frames:>3}/{s.secret_frames:<4} {s.mean_false_mask:>10.3f} "
                f"{r.p50_ms:>8.2f} {r.p95_ms:>8.2f} {r.fps:>8.0f}"
            )


if __name__ == "__main__":
    main()
