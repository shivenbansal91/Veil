import random
import string

import pytest

from bench.scenario import FILLER, TERMINAL_FILLER, _fake_values
from engine.detect.rules import find_secrets


def secret_spans(line):
    return {line[m.start : m.end] for m in find_secrets(line)}


@pytest.mark.parametrize("seed", range(20))
def test_every_planted_secret_format_is_found(seed):
    v = _fake_values(random.Random(seed))
    lines = {
        f"GITHUB_TOKEN={v['github_pat']}": v["github_pat"],
        f"AWS_ACCESS_KEY_ID={v['aws_access_key']}": v["aws_access_key"],
        f"STRIPE_SECRET_KEY={v['stripe_live']}": v["stripe_live"],
        f"DB_PASSWORD={v['password']}": v["password"],
        v["slack_bot"]: v["slack_bot"],
        v["aws_secret_key"]: v["aws_secret_key"],
        f'$ curl -s -H "Authorization: Bearer {v["jwt"]}" https://api.example.test/v1/me': v["jwt"],
        f"$ psql postgres://admin:{v['password']}@db.internal:5432/app": v["password"],
        f"INFO token refreshed: {v['github_pat']}": v["github_pat"],
    }
    for line, secret in lines.items():
        spans = find_secrets(line)
        assert any(line[m.start : m.end] == secret or secret in line[m.start : m.end] for m in spans), line


def test_ocr_lookalike_character_does_not_split_a_token():
    # found by VEIL-Bench: RapidOCR read "0" as Greek theta, and the 8 chars
    # before it stayed unmasked
    line = "yAmg5GnMθbbjUkTRIo/ZHoPzKu0dt94t2t8VAqa4"
    assert [(m.start, m.end) for m in find_secrets(line)] == [(0, len(line))]


def test_second_half_of_a_wrapped_token_is_found():
    rng = random.Random(0)
    tail = "".join(rng.choices(string.ascii_letters + string.digits + "-_", k=40))
    assert secret_spans(tail)


def test_quoted_value_after_a_secret_name_is_found_when_ocr_drops_the_equals():
    # VS Code, 14-15 px: OCR read `api_key` and `'hello1234sk-23455555'`, no `=`
    assert secret_spans("api_key 'hello1234sk-23455555'") == {"hello1234sk-23455555"}
    assert find_secrets("password manager settings") == []  # unquoted: prose, not a value


@pytest.mark.parametrize("line", FILLER + TERMINAL_FILLER)
def test_bench_filler_is_not_flagged(line):
    assert find_secrets(line) == []


@pytest.mark.parametrize(
    "line",
    [
        "requirements_development_versions.txt",
        "def get_api_key_from_environment(self):",
        "password = os.environ['DB_PASSWORD']",  # a lookup, not a value
        "commit 3f9c2a1b7d4e",
    ],
)
def test_ordinary_code_is_not_flagged(line):
    assert find_secrets(line) == [], secret_spans(line)
