"""Deterministic synthetic editor and terminal sessions with planted fake secrets.

Every frame carries pixel ground truth: the exact rectangle of each secret visible
in it, so scoring compares pixels and never depends on OCR. Secrets are random
fakes from a seeded RNG: same seed, same session, byte for byte.
"""

import math
import random
import string
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 14)
LINE_H = 19
TAB_BAR_H = 35
STATUS_BAR_H = 22
TEXT_X = 60
TOAST_W, TOAST_H = 520, 56
CHAR_W = FONT.getlength("M")  # Consolas is monospaced
ALNUM = string.ascii_letters + string.digits
B64URL = ALNUM + "-_"

# a row of text is a list of runs: (text, secret kind or None, colour)
Run = tuple[str, str | None, tuple[int, int, int]]
PLAIN, KEY, VALUE, TERM = (212, 212, 212), (156, 220, 254), (206, 145, 120), (204, 204, 204)

FILLER = [
    "import os",
    "from dataclasses import dataclass",
    "",
    "# fall back to defaults when the variable is unset",
    "@dataclass",
    "class Settings:",
    "    region: str = os.getenv('AWS_REGION', 'ap-south-1')",
    "    timeout_s: float = float(os.getenv('TIMEOUT', '2.5'))",
    "    retries: int = 3",
    "def connect(settings: Settings) -> Client:",
    "    client = Client(region=settings.region, timeout=settings.timeout_s)",
    "    for attempt in range(settings.retries):",
    "        if client.ping():",
    "            return client",
    "        time.sleep(0.5 * (attempt + 1))",
    "    raise ConnectionError(f'gave up after {settings.retries} tries')",
]

ENV_KEYS = {
    "github_pat": "GITHUB_TOKEN",
    "aws_access_key": "AWS_ACCESS_KEY_ID",
    "stripe_live": "STRIPE_SECRET_KEY",
    "password": "DB_PASSWORD",
}

TERMINAL_FILLER = [
    "$ git status",
    "On branch main",
    "nothing to commit, working tree clean",
    "$ python -m pytest -q",
    "........................................ [100%]",
    "40 passed in 3.21s",
    "$ pip install -r requirements.txt",
    "Requirement already satisfied: numpy in ./.venv/lib/site-packages (2.5.4)",
    "$ docker ps",
    "CONTAINER ID   IMAGE          STATUS         PORTS",
    "3f9c2a1b7d4e   postgres:17    Up 2 hours     0.0.0.0:5432->5432/tcp",
    "$ ls",
    "README.md  app  requirements.txt  tests",
]


class Rect(NamedTuple):
    """Half-open pixel box: x0 <= x < x1, y0 <= y < y1."""

    x0: int
    y0: int
    x1: int
    y1: int


@dataclass(frozen=True)
class Secret:
    kind: str
    value: str
    rect: Rect
    # exactly which pixels inside rect are the secret's glyphs, recorded while
    # rendering so the scorer never has to guess them from colours
    ink: np.ndarray = field(compare=False, repr=False)


@dataclass(frozen=True)
class Frame:
    index: int
    pixels: np.ndarray  # H x W x 3, uint8 RGB
    secrets: tuple[Secret, ...]


def _fake_values(rng: random.Random) -> dict[str, str]:
    def pick(alphabet: str, n: int) -> str:
        return "".join(rng.choices(alphabet, k=n))

    return {
        "github_pat": "ghp_" + pick(ALNUM, 36),
        "aws_access_key": "AKIA" + pick(string.ascii_uppercase + string.digits, 16),
        "stripe_live": "sk_live_" + pick(ALNUM, 24),
        "slack_bot": f"xoxb-{pick(string.digits, 11)}-{pick(string.digits, 12)}-{pick(ALNUM, 24)}",
        "password": pick(ALNUM + "!#%^&*", 18),
        "aws_secret_key": pick(ALNUM + "/+", 40),
        "jwt": "eyJhbGciOiJIUzI1NiJ9." + pick(B64URL, 64) + "." + pick(B64URL, 43),
    }


def _scroll_offset(i: int, n_frames: int) -> int:
    # hold, then scroll 2 lines per frame, then hold: exercises static masks,
    # moving secrets, and secrets entering/leaving the viewport
    third = n_frames // 3
    return 2 * min(max(i - third, 0), third)


def editor_session(seed: int, n_frames: int = 30, size: tuple[int, int] = (1280, 720)) -> list[Frame]:
    """A dark-theme editor that scrolls past .env lines, plus a short toast."""
    rng = random.Random(seed)
    width, height = size
    values = _fake_values(rng)
    visible = (height - TAB_BAR_H - STATUS_BAR_H) // LINE_H
    reach = visible + _scroll_offset(n_frames - 1, n_frames)

    lines: list[str | tuple[str, str]] = [rng.choice(FILLER) for _ in range(reach + visible)]
    # insert highest position first so earlier inserts can push each line down by
    # at most len(ENV_KEYS) - 1, keeping every secret within scroll reach
    positions = sorted(rng.sample(range(2, reach - len(ENV_KEYS)), len(ENV_KEYS)), reverse=True)
    for pos, (kind, key) in zip(positions, ENV_KEYS.items()):
        lines.insert(pos, (key, kind))
    toast_start = rng.randrange(n_frames // 2, n_frames - 1)

    frames = []
    for i in range(n_frames):
        img = Image.new("RGB", size, (30, 30, 30))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, width, TAB_BAR_H - 1], fill=(45, 45, 45))
        draw.text((12, 10), ".env", font=FONT, fill=(255, 255, 255))
        draw.rectangle([0, height - STATUS_BAR_H, width, height], fill=(0, 122, 204))

        secrets = []
        offset = _scroll_offset(i, n_frames)
        for row, line in enumerate(lines[offset : offset + visible]):
            y = TAB_BAR_H + 4 + row * LINE_H
            draw.text((8, y), f"{offset + row + 1:>4}", font=FONT, fill=(133, 133, 133))
            if isinstance(line, tuple):
                key, kind = line
                runs = [(key + "=", None, KEY), (values[kind], kind, VALUE)]
            else:
                runs = [(line, None, PLAIN)]
            secrets += _draw_row(draw, TEXT_X, y, runs)

        # toasts appear for ~1 s in real life; here, two frames
        if toast_start <= i < toast_start + 2:
            x0, y0 = width - TOAST_W - 12, height - STATUS_BAR_H - TOAST_H - 12
            toast = Rect(x0, y0, x0 + TOAST_W + 1, y0 + TOAST_H + 1)
            # the toast hides whatever ink it covers; a secret only partly under it
            # stays in the ground truth with its visible pixels
            secrets = [s for s in (_occlude(s, toast) for s in secrets) if s.ink.any()]
            draw.rectangle([x0, y0, x0 + TOAST_W, y0 + TOAST_H], fill=(37, 37, 38), outline=(69, 69, 69))
            draw.text((x0 + 12, y0 + 8), "Slack: deploy bot token", font=FONT, fill=(204, 204, 204))
            secrets += _draw_row(
                draw, x0 + 12, y0 + 30, [(values["slack_bot"], "slack_bot", (240, 240, 240))]
            )

        frames.append(Frame(i, np.asarray(img).copy(), tuple(secrets)))
    return frames


def terminal_session(seed: int, n_frames: int = 30, size: tuple[int, int] = (1280, 720)) -> list[Frame]:
    """A terminal whose output grows two rows per frame and scrolls when full.

    Long lines wrap at the terminal width, so a secret can be split across rows:
    each visible part is its own ground-truth entry with the same kind.
    """
    rng = random.Random(seed)
    width, height = size
    values = _fake_values(rng)
    pad = 10
    cols = int((width - 2 * pad) // CHAR_W)
    visible = (height - 2 * pad) // LINE_H

    commands: list[list[Run]] = [
        [("$ echo $AWS_SECRET_ACCESS_KEY", None, TERM)],
        [(values["aws_secret_key"], "aws_secret_key", TERM)],
        [('$ curl -s -H "Authorization: Bearer ', None, TERM), (values["jwt"], "jwt", TERM)]
        + [('" https://api.example.test/v1/me', None, TERM)],
        [("$ psql postgres://admin:", None, TERM), (values["password"], "password", TERM)]
        + [("@db.internal:5432/app", None, TERM)],
        [("INFO token refreshed: ", None, TERM), (values["github_pat"], "github_pat", TERM)],
    ]
    total_rows = 6 + 2 * n_frames
    script: list[list[Run]] = [[(rng.choice(TERMINAL_FILLER), None, TERM)] for _ in range(total_rows)]
    # keep each secret command early enough that it is printed before the session ends
    for pos, command in zip(sorted(rng.sample(range(2, total_rows // 2), 4), reverse=True), commands[1:]):
        script.insert(pos, command)
    script.insert(script.index(commands[1]), commands[0])
    rows = [row for line in script for row in _wrap(line, cols)]

    frames = []
    for i in range(n_frames):
        img = Image.new("RGB", size, (12, 12, 12))
        draw = ImageDraw.Draw(img)
        shown = rows[: 6 + 2 * i][-visible:]
        secrets = []
        for r, runs in enumerate(shown):
            secrets += _draw_row(draw, pad, pad + r * LINE_H, runs)
        frames.append(Frame(i, np.asarray(img).copy(), tuple(secrets)))
    return frames


def _wrap(line: list[Run], cols: int) -> list[list[Run]]:
    """Split a line into rows of at most cols characters, keeping run boundaries."""
    rows: list[list[Run]] = [[]]
    used = 0
    for text, kind, colour in line:
        while text:
            if used == cols:
                rows.append([])
                used = 0
            part, text = text[: cols - used], text[cols - used :]
            rows[-1].append((part, kind, colour))
            used += len(part)
    return rows


def _draw_row(draw: ImageDraw.ImageDraw, x: float, y: float, runs: list[Run]) -> list[Secret]:
    secrets = []
    for text, kind, colour in runs:
        if kind is None:
            draw.text((x, y), text, font=FONT, fill=colour)
        else:
            secrets.append(_draw_secret(draw, (x, y), kind, text, colour))
        x += draw.textlength(text, font=FONT)
    return secrets


def _draw_secret(draw: ImageDraw.ImageDraw, xy: tuple[float, float], kind: str, value: str, colour) -> Secret:
    draw.text(xy, value, font=FONT, fill=colour)
    bx0, by0, bx1, by1 = draw.textbbox(xy, value, font=FONT)
    # round outward so anti-aliased edge pixels stay inside the ground-truth box
    rect = Rect(math.floor(bx0), math.floor(by0), math.ceil(bx1), math.ceil(by1))
    layer = Image.new("L", (rect.x1 - rect.x0, rect.y1 - rect.y0), 0)
    ImageDraw.Draw(layer).text((xy[0] - rect.x0, xy[1] - rect.y0), value, font=FONT, fill=255)
    return Secret(kind, value, rect, np.asarray(layer) > 0)


def _occlude(secret: Secret, cover: Rect) -> Secret:
    r = secret.rect
    x0, y0 = max(r.x0, cover.x0), max(r.y0, cover.y0)
    x1, y1 = min(r.x1, cover.x1), min(r.y1, cover.y1)
    if x0 >= x1 or y0 >= y1:
        return secret
    ink = secret.ink.copy()
    ink[y0 - r.y0 : y1 - r.y0, x0 - r.x0 : x1 - r.x0] = False
    return replace(secret, ink=ink)
