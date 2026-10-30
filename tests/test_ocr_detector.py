import numpy as np
import pytest

from engine.detect.ocr import BAND_MARGIN, Line, ScreenDetector, secret_boxes
from engine.gate import RawFrame

H, W = 300, 400
TOKEN = "ghp_" + "aB3dE5fG7hJ9kL1mN2pQ4rS6tU8vW0xY2zA4"


class FakeOcr:
    """Reports a secret line wherever a white marker row band is drawn."""

    def __init__(self):
        self.calls = []

    def __call__(self, band):
        self.calls.append(band.shape[0])
        rows = np.flatnonzero((band == 255).all(axis=(1, 2)) | (band == 255).any(axis=(1, 2)))
        if not len(rows):
            return []
        y0, y1 = int(rows.min()), int(rows.max()) + 1
        return [Line("GITHUB_TOKEN=" + TOKEN, (10, y0, 390, y1), None)]


def frame(marker_rows=None, noise_row=None):
    pixels = np.full((H, W, 3), 30, dtype=np.uint8)
    if marker_rows:
        pixels[marker_rows[0] : marker_rows[1], 10:390] = 255
    if noise_row is not None:
        pixels[noise_row, 0:5] = 100  # a change elsewhere, e.g. a cursor blink
    return RawFrame(0, 0.0, pixels)


def test_first_frame_reads_the_whole_frame():
    ocr = FakeOcr()
    masks = ScreenDetector(ocr)(frame((100, 115)))
    assert ocr.calls == [H]
    assert masks == [(10, 100, 390, 115)]


def test_unchanged_frame_is_not_read_again_and_keeps_its_masks():
    ocr = FakeOcr()
    detect = ScreenDetector(ocr)
    first = detect(frame((100, 115)))
    assert detect(frame((100, 115))) == first
    assert ocr.calls == [H]


def test_change_elsewhere_reads_only_that_band_and_keeps_the_mask():
    ocr = FakeOcr()
    detect = ScreenDetector(ocr)
    detect(frame((100, 115)))
    masks = detect(frame((100, 115), noise_row=250))
    assert ocr.calls[1:] == [min(250 + 1 + BAND_MARGIN, H) - (250 - BAND_MARGIN)]
    assert masks == [(10, 100, 390, 115)]


def test_secret_moving_away_drops_its_old_mask():
    ocr = FakeOcr()
    detect = ScreenDetector(ocr)
    detect(frame((100, 115)))
    masks = detect(frame((200, 215)))  # scrolled: old spot and new spot both changed
    assert masks == [(10, 200, 390, 215)]


def test_band_touching_a_mask_rereads_the_whole_masked_line():
    ocr = FakeOcr()
    detect = ScreenDetector(ocr)
    detect(frame((100, 160)))
    # change one row just below the mask's band reach: the band must still cover the mask
    detect(frame((100, 160), noise_row=160 + BAND_MARGIN - 1))
    assert ocr.calls[1] >= 160 + BAND_MARGIN - (100) - 1


def test_ocr_failure_raises_and_the_next_frame_is_read_in_full():
    calls = []

    def flaky(band):
        calls.append(band.shape[0])
        if len(calls) == 2:
            raise RuntimeError("ocr crashed")
        return []

    detect = ScreenDetector(flaky)
    detect(frame())
    with pytest.raises(RuntimeError):
        detect(frame(noise_row=10))
    detect(frame(noise_row=10))
    assert calls[-1] == H


def test_span_maps_to_its_characters_with_slack():
    text = "A=" + "x" * 8 + " B=sk_live_" + "Z9" * 10
    nonspace = [c for c in text if c != " "]
    chars = [(i * 10, 0, i * 10 + 9, 20) for i in range(len(nonspace))]
    (box,) = secret_boxes([Line(text, (0, 0, len(nonspace) * 10, 20), chars)])
    start = nonspace.index("s")  # the stripe key starts at the 13th non-space char
    assert box[0] <= start * 10 and box[2] >= len(nonspace) * 10 - 1
    assert box[0] > 3 * 10  # "A=xxxxxxxx" stays visible


def test_char_count_mismatch_masks_the_whole_line():
    text = "GITHUB_TOKEN=" + TOKEN
    (box,) = secret_boxes([Line(text, (5, 6, 300, 25), [(0, 0, 1, 1)] * 3)])
    assert box == (5, 6, 300, 25)


def test_rapidocr_adapter_reads_a_rendered_token():
    from PIL import Image, ImageDraw, ImageFont

    from engine.detect.ocr import rapidocr

    font = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 14)
    img = Image.new("RGB", (640, 40), (30, 30, 30))
    line = "STRIPE_SECRET_KEY=sk_live_" + "Q7wE9rT1yU3iO5pA2sD4fG6h"
    ImageDraw.Draw(img).text((20, 12), line, font=font, fill=(206, 145, 120))
    (box,) = secret_boxes(rapidocr()(np.asarray(img)))
    key_end = 20 + font.getlength("STRIPE_SECRET_KEY=")
    assert key_end - 8 <= box[0] <= key_end + 2  # mask starts at the value, not the name
    assert box[2] >= 20 + font.getlength(line) - 1
