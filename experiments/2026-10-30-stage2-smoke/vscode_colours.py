"""OCR the dummy .env lines drawn in VS Code Dark+ colours, at several font sizes.

Reproduces the live miss: with syntax colours RapidOCR splits `api_key` from
`= 'value'`, and sometimes drops the `=`. Prints each OCR piece and the masks.

Run: .venv\\Scripts\\python experiments\\2026-10-30-stage2-smoke\\vscode_colours.py
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine.detect.ocr import rapidocr, secret_boxes

BG, VAR, OP, STR = (31, 31, 31), (156, 220, 254), (212, 212, 212), (206, 145, 120)
LINES = [  # the fake values from Shiven's live test file
    [("password", VAR), ("= ", OP), ("'#12345hello'", STR)],
    [("api_key", VAR), (" = ", OP), ("'hello1234sk-23455555'", STR)],
    [("password", VAR), (" = ", OP), ("'hello123###'", STR)],
]

ocr = rapidocr({"EngineConfig.onnxruntime.use_cuda": True})
for size in (14, 15, 18, 21):
    font = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", size)
    img = Image.new("RGB", (900, size * 2 * len(LINES) + 20), BG)
    draw = ImageDraw.Draw(img)
    for i, parts in enumerate(LINES):
        x, y = 60, 10 + i * size * 2
        for text, colour in parts:
            draw.text((x, y), text, font=font, fill=colour)
            x += draw.textlength(text, font=font)
    pieces = ocr(np.asarray(img))
    print(f"{size} px: {len(pieces)} OCR pieces, {len(secret_boxes(pieces))} of {len(LINES)} masked")
    for piece in pieces:
        print(f"    {piece.text!r} at x {piece.box[0]}-{piece.box[2]}, y {piece.box[1]}-{piece.box[3]}")
