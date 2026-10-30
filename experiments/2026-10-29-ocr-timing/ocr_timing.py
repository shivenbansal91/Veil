"""Time RapidOCR on a synthetic 1080p dark-theme editor screen, GPU vs CPU.

Secrets are random fakes generated per run and never written to disk.
Run: .venv\\Scripts\\python experiments\\2026-10-29-ocr-timing\\ocr_timing.py
"""

import random
import statistics
import string
import time

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont
from rapidocr import RapidOCR

W, H = 1920, 1080
FONT = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 14)
LINE_H = 19
ALNUM = string.ascii_letters + string.digits
UPPER_DIGITS = string.ascii_uppercase + string.digits

CODE = [
    "import os",
    "from dataclasses import dataclass",
    "",
    "# load settings from the environment, fall back to defaults",
    "@dataclass",
    "class Settings:",
    "    region: str = os.getenv('AWS_REGION', 'ap-south-1')",
    "    timeout_s: float = float(os.getenv('TIMEOUT', '2.5'))",
    "    retries: int = 3",
    "",
    "def connect(settings: Settings) -> Client:",
    "    client = Client(region=settings.region, timeout=settings.timeout_s)",
    "    for attempt in range(settings.retries):",
    "        if client.ping():",
    "            return client",
    "        time.sleep(0.5 * (attempt + 1))",
    "    raise ConnectionError(f'gave up after {settings.retries} tries')",
    "",
]


def fake(prefix: str, n: int, alphabet: str = ALNUM) -> str:
    return prefix + "".join(random.choices(alphabet, k=n))


def render() -> tuple[np.ndarray, list[str]]:
    secrets = [fake("ghp_", 36), fake("AKIA", 16, UPPER_DIGITS), fake("sk_live_", 24)]
    env = [
        f"GITHUB_TOKEN={secrets[0]}",
        f"AWS_ACCESS_KEY_ID={secrets[1]}",
        f"STRIPE_SECRET_KEY={secrets[2]}",
    ]
    img = Image.new("RGB", (W, H), "#1e1e1e")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 48, H], fill="#333333")
    d.rectangle([48, 0, 300, H], fill="#252526")
    for i, name in enumerate([".env", "settings.py", "client.py", "README.md"]):
        d.text((70, 40 + i * 22), name, font=FONT, fill="#cccccc")
    d.rectangle([300, 0, W, 35], fill="#2d2d2d")
    d.text((315, 10), "settings.py", font=FONT, fill="#ffffff")
    d.rectangle([0, H - 22, W, H], fill="#007acc")

    # Whole-line colouring only; real syntax highlighting splits tokens into more
    # colours, which is a harder case for OCR.
    lines = (CODE * 3)[:20] + env + (CODE * 3)[:30]
    for i, line in enumerate(lines[: (H - 35 - 22) // LINE_H]):
        y = 40 + i * LINE_H
        d.text((310, y), f"{i + 1:>3}", font=FONT, fill="#858585")
        colour = "#6a9955" if line.lstrip().startswith("#") else "#d4d4d4"
        if "=" in line and line.split("=")[0].isupper():
            key, value = line.split("=", 1)
            d.text((360, y), key + "=", font=FONT, fill="#9cdcfe")
            d.text((360 + d.textlength(key + "=", font=FONT), y), value, font=FONT, fill="#ce9178")
        else:
            d.text((360, y), line, font=FONT, fill=colour)
    return np.ascontiguousarray(np.array(img)[:, :, ::-1]), secrets


def time_engine(engine: RapidOCR, frame: np.ndarray, runs: int = 20, warmup: int = 3):
    for _ in range(warmup):
        engine(frame)
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        result = engine(frame)
        times.append((time.perf_counter() - t) * 1000)
    return times, result


def main() -> None:
    ort.preload_dlls()
    print("onnxruntime", ort.__version__, ort.get_available_providers())
    frame, secrets = render()
    engines = {
        "gpu": RapidOCR(params={"EngineConfig.onnxruntime.use_cuda": True}),
        "cpu": RapidOCR(),
    }
    for name, engine in engines.items():
        times, result = time_engine(engine, frame)
        p95 = statistics.quantiles(times, n=20)[18]
        found = sum(any(s in t for t in result.txts) for s in secrets)
        print(
            f"{name}: median {statistics.median(times):.0f} ms, p95 {p95:.0f} ms, "
            f"{len(result.txts)} boxes, exact secret reads {found}/{len(secrets)}"
        )


if __name__ == "__main__":
    main()
