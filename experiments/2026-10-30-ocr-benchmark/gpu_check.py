"""GPU readiness: CUDA EP present, a real model runs on it, output matches CPU,
RapidOCR runs end to end on CUDA. Prints latency and GPU memory.

Run: .venv\\Scripts\\python experiments\\2026-10-30-ocr-benchmark\\gpu_check.py
"""

import statistics
import subprocess
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


def gpu_memory_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip())


def median_ms(fn, runs: int = 20) -> float:
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return statistics.median(times)


def main() -> None:
    ort.preload_dlls()
    providers = ort.get_available_providers()
    print("onnxruntime", ort.__version__, providers)
    assert "CUDAExecutionProvider" in providers, "CUDA EP missing"

    import rapidocr

    det_model = next((Path(rapidocr.__file__).parent / "models").glob("*det*.onnx"))
    print("model", det_model.name)
    base_mib = gpu_memory_mib()

    gpu = ort.InferenceSession(str(det_model), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    cpu = ort.InferenceSession(str(det_model), providers=["CPUExecutionProvider"])
    assert gpu.get_providers()[0] == "CUDAExecutionProvider", gpu.get_providers()

    name = gpu.get_inputs()[0].name
    x = np.random.default_rng(0).random((1, 3, 736, 1280), dtype=np.float32)
    gpu_out = gpu.run(None, {name: x})[0]
    cpu_out = cpu.run(None, {name: x})[0]
    print(f"det model GPU vs CPU max abs diff: {np.abs(gpu_out - cpu_out).max():.2e}")
    print(
        f"det model 1280x736: GPU {median_ms(lambda: gpu.run(None, {name: x})):.1f} ms, "
        f"CPU {median_ms(lambda: cpu.run(None, {name: x}), runs=5):.1f} ms"
    )
    print(f"GPU memory: {base_mib} MiB before, {gpu_memory_mib()} MiB with det session loaded")

    from rapidocr import RapidOCR

    engine = RapidOCR(params={"EngineConfig.onnxruntime.use_cuda": True})
    img = np.full((120, 900, 3), 30, dtype=np.uint8)
    import cv2

    cv2.putText(
        img, "GITHUB_TOKEN=ghp_FAKE0000example", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (220, 220, 220), 2
    )
    result = engine(img)  # models load lazily on the first call
    print("rapidocr read:", result.txts)
    for part in ("text_det", "text_cls", "text_rec"):
        print(part, getattr(engine, part).session.session.get_providers()[0])
    print(f"GPU memory with RapidOCR loaded: {gpu_memory_mib()} MiB")


if __name__ == "__main__":
    main()
