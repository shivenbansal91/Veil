"""Record the protected view the way a meeting app would see it (WGC window capture),
saving a PNG each time the picture changes. Analyse afterwards with check.py.

Run: .venv\\Scripts\\python experiments\\2026-10-30-stage2-smoke\\record.py OUT_DIR SECONDS [WINDOW]
"""

import hashlib
import sys
import time
from pathlib import Path

import cv2
from windows_capture import WindowsCapture

out_dir, seconds = Path(sys.argv[1]), float(sys.argv[2])
window = sys.argv[3] if len(sys.argv) > 3 else "VEIL protected view"
out_dir.mkdir(parents=True, exist_ok=True)
last = {"digest": b""}
counts = {"frames": 0, "changes": 0}
start = time.monotonic()
capture = WindowsCapture(cursor_capture=False, draw_border=False, window_name=window)


@capture.event
def on_frame_arrived(frame, control):
    counts["frames"] += 1
    bgr = frame.frame_buffer[:, :, :3]
    digest = hashlib.blake2b(bgr.tobytes(), digest_size=16).digest()
    if digest != last["digest"]:
        last["digest"] = digest
        counts["changes"] += 1
        cv2.imwrite(str(out_dir / f"{time.monotonic() - start:08.3f}.png"), bgr)
    if time.monotonic() - start > seconds:
        control.stop()


@capture.event
def on_closed():
    pass


capture.start()
print(f"captured {counts['frames']} frames, {counts['changes']} changes of picture, in {out_dir}")
