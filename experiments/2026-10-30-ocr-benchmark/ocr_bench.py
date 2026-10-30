"""RapidOCR on synthetic 1080p editor and terminal frames: CPU vs GPU, settings,
and how well the planted (fake) secrets are read.

Accuracy per secret part: exact = the value appears verbatim in some OCR line;
CER = character error rate of the best-aligned window of an OCR line.
"Dirty region" = 3 full-width text rows around one secret, the workload the
gate actually sees once only changed lines are OCR'd (CLAUDE.md §5.3).

Run: .venv\\Scripts\\python experiments\\2026-10-30-ocr-benchmark\\ocr_bench.py
"""

import difflib
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from rapidocr import RapidOCR

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bench.scenario import LINE_H, Frame, editor_session, terminal_session

GPU = {"EngineConfig.onnxruntime.use_cuda": True}
FAST = {"Global.use_cls": False, "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC"}
DET_NO_UPSCALE = {"Det.limit_type": "max", "Det.limit_side_len": 1920}
CONFIGS = {
    "cpu default": ({}, 3),
    "cpu no-cls": ({"Global.use_cls": False}, 3),
    "gpu default": (GPU, 10),
    "gpu no-cls heuristic": (GPU | FAST, 10),
    "gpu no-cls heuristic rec-batch-24": (GPU | FAST | {"Rec.rec_batch_num": 24}, 10),
    # the default limit_type "min" upscales any image whose short side is under
    # 736 px, so a 64 px strip becomes ~22000x736 before detection
    "cpu no-cls det-max-1920": ({"Global.use_cls": False} | DET_NO_UPSCALE, 3),
    "gpu no-cls heuristic det-max-1920": (GPU | FAST | DET_NO_UPSCALE, 10),
}


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def best_cer(value: str, lines: tuple[str, ...]) -> float:
    best = 1.0
    for line in lines:
        m = difflib.SequenceMatcher(None, line, value, autojunk=False).find_longest_match()
        if m.size < 4:
            continue
        start = max(m.a - m.b, 0)
        best = min(best, levenshtein(value, line[start : start + len(value)]) / len(value))
    return best


def pick_frame(session: list[Frame]) -> Frame:
    return max(session, key=lambda f: len(f.secrets))


def dirty_region(frame: Frame) -> tuple[np.ndarray, Frame]:
    s = max(frame.secrets, key=lambda s: len(s.value))
    y0 = max(s.rect.y0 - LINE_H - 4, 0)
    y1 = min(s.rect.y1 + LINE_H + 4, frame.pixels.shape[0])
    return frame.pixels[y0:y1], Frame(frame.index, frame.pixels[y0:y1], (s,))


def gpu_memory_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip())


def measure(engine: RapidOCR, rgb: np.ndarray, runs: int):
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    engine(bgr)  # warm-up: lazy model load + cuDNN algorithm search
    engine(bgr)
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        result = engine(bgr)
        times.append((time.perf_counter() - t) * 1000)
    return statistics.median(times), result


def accuracy(frame: Frame, txts: tuple[str, ...]) -> tuple[int, float]:
    exact = sum(any(s.value in t for t in txts) for s in frame.secrets)
    cer = statistics.mean(best_cer(s.value, txts) for s in frame.secrets)
    return exact, cer


def main() -> None:
    ort.preload_dlls()
    scenes = {
        "editor": pick_frame(editor_session(seed=1, n_frames=30, size=(1920, 1080))),
        "terminal": pick_frame(terminal_session(seed=1, n_frames=30, size=(1920, 1080))),
    }
    print(f"GPU memory idle: {gpu_memory_mib()} MiB")
    print(f"{'config':<36} {'input':<16} {'median ms':>10} {'boxes':>6} {'exact':>7} {'mean CER':>9}")
    only = sys.argv[1] if len(sys.argv) > 1 else ""  # substring filter on config names
    for label, (params, runs) in CONFIGS.items():
        if only not in label:
            continue
        engine = RapidOCR(params=params | {"Global.log_level": "error"})
        for name, frame in scenes.items():
            for kind, (rgb, truth) in {
                "full 1920x1080": (frame.pixels, frame),
                "dirty 3 rows": dirty_region(frame),
            }.items():
                ms, result = measure(engine, rgb, runs)
                txts = result.txts or ()
                exact, cer = accuracy(truth, txts)
                print(
                    f"{label:<36} {name + ' ' + kind:<16.16} {ms:>10.1f} {len(txts):>6} "
                    f"{exact:>3}/{len(truth.secrets):<3} {cer:>9.3f}"
                )
        if params.get("EngineConfig.onnxruntime.use_cuda"):
            print(f"  GPU memory with this engine loaded: {gpu_memory_mib()} MiB")
        del engine


if __name__ == "__main__":
    main()
