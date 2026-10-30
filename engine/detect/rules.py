"""Regex and entropy rules for secret formats (CLAUDE.md §6.1), on OCR text.

Patterns follow gitleaks' rules in spirit, loosened where OCR text differs from
source text: no word-boundary anchors, since OCR may glue a token to its
neighbours or drop the space between them.
"""

import math
import re
from collections import Counter
from typing import NamedTuple


class Match(NamedTuple):
    start: int  # character span in the OCR line, half-open
    end: int
    kind: str


# (kind, pattern); the secret is group "s" when present, else the whole match
RULES = [
    ("github_token", r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    ("aws_access_key", r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    ("stripe_key", r"[sr]k_(?:live|test)_[A-Za-z0-9]{20,}"),
    ("slack_token", r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*\.?[A-Za-z0-9_-]*"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("connection_string", r"://[^\s:/@]+:(?P<s>[^\s@]+)@"),
    ("bearer", r"(?i:bearer)\s+(?P<s>[A-Za-z0-9._~+/=-]{12,})"),
    # KEY=value / key: value where the name says it is secret
    (
        "assignment",
        (
            r"(?i:[A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api_?key|access_?key|private_?key))"
            # the value must end the token, so `pwd = os.environ[...]` is code, not a value
            r"[\"']?\s*[=:]\s*[\"']?(?P<s>[^\s\"'()\[\]{}]{6,})(?=[\s\"',;]|$)"
        ),
    ),
]
COMPILED = [(kind, re.compile(pattern)) for kind, pattern in RULES]

# A bare high-entropy run, e.g. `echo $AWS_SECRET_ACCESS_KEY` output or the
# second half of a token wrapped onto the next line. 20 chars because shorter
# random strings (git hashes, docker ids) are common and harmless on screen.
# "=" only as base64 padding (not KEY=value); non-ASCII because OCR swaps in
# look-alikes (0 read as Greek theta) and those must not split the token
CANDIDATE = re.compile(r"[A-Za-z0-9+/_\u0080-￿-]{20,}={0,2}")
MIN_ENTROPY = 3.5  # bits/char; identifiers like site_packages_directory score below


def shannon_entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())


def find_secrets(line: str) -> list[Match]:
    matches = []
    for kind, pattern in COMPILED:
        for m in pattern.finditer(line):
            group = "s" if "s" in pattern.groupindex else 0
            matches.append(Match(m.start(group), m.end(group), kind))
    for m in CANDIDATE.finditer(line):
        token = m.group()
        has_letter_and_digit = any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
        if has_letter_and_digit and shannon_entropy(token) >= MIN_ENTROPY:
            matches.append(Match(m.start(), m.end(), "high_entropy"))
    return matches
