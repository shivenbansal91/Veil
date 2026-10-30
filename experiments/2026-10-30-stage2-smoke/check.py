"""Is any planted secret readable in the recorded protected view?

OCR-based (CLAUDE.md §9.1 secondary check): the recorded window has a title
bar and is scaled, so bench pixel geometry doesn't apply. It uses RapidOCR
defaults (cls on, default detector), not the engine's settings, but it is the
same model family, so it shares some blind spots with the detector.

Run: .venv\\Scripts\\python experiments\\2026-10-30-stage2-smoke\\check.py OUT_DIR [seed]
"""

import difflib
import random
import sys
from pathlib import Path

import cv2
import onnxruntime as ort
from rapidocr import RapidOCR

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bench.scenario import _fake_values

READABLE_CHARS = 10  # this many consecutive characters of a secret count as a leak

out_dir = Path(sys.argv[1])
values = _fake_values(random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 1))
planted = {k: values[k] for k in ("github_pat", "aws_access_key", "stripe_live", "password", "slack_bot")}
ort.preload_dlls()
engine = RapidOCR(params={"EngineConfig.onnxruntime.use_cuda": True, "Global.log_level": "error"})

leaks, black, read_any = [], 0, 0
files = sorted(out_dir.glob("*.png"))
for path in files:
    img = cv2.imread(str(path))
    if img.mean() < 2:
        black += 1
        continue
    text = "\n".join(engine(img).txts or ())
    read_any += bool(text)
    for kind, value in planted.items():
        m = difflib.SequenceMatcher(None, text, value, autojunk=False).find_longest_match()
        if m.size >= READABLE_CHARS:
            leaks.append((path.name, kind, value[m.b : m.b + m.size]))

print(f"{len(files)} recorded pictures: {black} black, {read_any} with readable text")
print(f"secrets readable (>= {READABLE_CHARS} consecutive chars): {len(leaks)}")
for name, kind, fragment in leaks:
    print(f"  {name} {kind}: {fragment}")
