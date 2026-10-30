"""Stage 1 acceptance: the scorer finds planted secrets in raw output and is not fooled."""

import numpy as np
import pytest

from bench.run import gate_pipeline
from bench.run import run as timed_run
from bench.scenario import editor_session, terminal_session
from bench.score import LEAK_INK_FRACTION, black_everything, no_protection, oracle_mask, score, surviving_ink

SESSIONS = {"editor": editor_session, "terminal": terminal_session}


@pytest.fixture(scope="module", params=SESSIONS)
def session(request):
    return SESSIONS[request.param](seed=7, n_frames=30)


def run(session, pipeline):
    return score(session, [pipeline(f) for f in session])


def test_same_seed_same_session():
    a, b = editor_session(seed=3, n_frames=4), editor_session(seed=3, n_frames=4)
    assert all(np.array_equal(x.pixels, y.pixels) and x.secrets == y.secrets for x, y in zip(a, b))
    c = editor_session(seed=4, n_frames=4)
    assert not np.array_equal(a[0].pixels, c[0].pixels)


def test_terminal_same_seed_same_session():
    a, b = terminal_session(seed=3, n_frames=12), terminal_session(seed=3, n_frames=12)
    assert all(np.array_equal(x.pixels, y.pixels) and x.secrets == y.secrets for x, y in zip(a, b))


def test_editor_covers_the_cases_it_claims():
    session = editor_session(seed=7, n_frames=30)
    kinds = {s.kind for f in session for s in f.secrets}
    assert kinds == {"github_pat", "aws_access_key", "stripe_live", "password", "slack_bot"}
    assert sum(any(s.kind == "slack_bot" for s in f.secrets) for f in session) == 2
    # scrolling moves at least one secret between frames
    rects = {s.kind: set() for f in session for s in f.secrets}
    for f in session:
        for s in f.secrets:
            rects[s.kind].add(s.rect)
    assert any(len(r) > 1 for r in rects.values())


def test_terminal_covers_the_cases_it_claims():
    session = terminal_session(seed=7, n_frames=30)
    kinds = {s.kind for f in session for s in f.secrets}
    assert kinds == {"aws_secret_key", "jwt", "password", "github_pat"}
    # the curl line is longer than the terminal is wide, so the token wraps:
    # one frame shows the jwt as two ground-truth parts on consecutive rows
    jwt_parts = max((tuple(s for s in f.secrets if s.kind == "jwt") for f in session), key=len)
    assert len(jwt_parts) == 2
    first, second = sorted(jwt_parts, key=lambda s: s.rect.y0)
    assert second.rect.y0 > first.rect.y0
    whole = first.value + second.value
    assert whole.startswith("eyJ") and len(whole) == len("eyJhbGciOiJIUzI1NiJ9.") + 64 + 1 + 43


def test_ground_truth_rects_hold_ink_inside_the_frame(session):
    h, w = session[0].pixels.shape[:2]
    for f in session:
        for s in f.secrets:
            assert 0 <= s.rect.x0 < s.rect.x1 <= w and 0 <= s.rect.y0 < s.rect.y1 <= h
            region = f.pixels[s.rect.y0 : s.rect.y1, s.rect.x0 : s.rect.x1]
            assert len(np.unique(region.reshape(-1, 3), axis=0)) > 2


def test_no_protection_leaks_every_secret_frame(session):
    result = run(session, no_protection)
    assert result.leaked_frames == result.secret_frames > 0
    for f, leaked in zip(session, result.leaks):
        assert {s.kind for s in f.secrets} <= leaked
    assert result.mean_false_mask == 0


def test_black_everything_leaks_nothing_and_hides_everything(session):
    result = run(session, black_everything)
    assert result.leaked_frames == 0
    assert result.mean_false_mask == pytest.approx(1.0)


def test_oracle_mask_leaks_nothing_and_hides_nothing_else(session):
    result = run(session, oracle_mask)
    assert result.leaked_frames == 0
    assert result.mean_false_mask == 0


def test_missing_one_secret_kind_leaks_exactly_those_frames(session):
    missed = "password"  # present in both scenarios

    def mask_all_but_one(frame):
        out = frame.pixels.copy()
        for s in frame.secrets:
            if s.kind != missed:
                out[s.rect.y0 : s.rect.y1, s.rect.x0 : s.rect.x1] = 0
        return out

    result = run(session, mask_all_but_one)
    assert any(result.leaks)
    for f, leaked in zip(session, result.leaks):
        assert leaked == ({missed} if any(s.kind == missed for s in f.secrets) else set())


def test_half_masked_secret_is_a_leak(session):
    frame = next(f for f in session if f.secrets)
    s = frame.secrets[0]
    out = frame.pixels.copy()
    mid = (s.rect.x0 + s.rect.x1) // 2
    out[s.rect.y0 : s.rect.y1, s.rect.x0 : mid] = 0
    region = (slice(s.rect.y0, s.rect.y1), slice(s.rect.x0, s.rect.x1))
    assert surviving_ink(frame.pixels[region], out[region], s.ink) > LEAK_INK_FRACTION


def test_frozen_output_showing_an_older_unmasked_frame_is_caught(session):
    # outputs[k] shows raw frame 0 for every k: the scorer must not rely on time alignment
    first = next(i for i, f in enumerate(session) if f.secrets)
    outputs = [session[first].pixels] * len(session)
    result = score(session, outputs)
    assert all(result.leaks)


def test_ink_is_where_the_glyphs_are(session):
    for f in session:
        for s in f.secrets:
            region = f.pixels[s.rect.y0 : s.rect.y1, s.rect.x0 : s.rect.x1]
            background = region[~s.ink]
            assert s.ink.shape == region.shape[:2] and s.ink.any()
            # glyph pixels are not background-coloured; most of the box is background
            assert not np.isin(region[s.ink].view("V3"), background.view("V3")).all()
            assert s.ink.mean() < 0.6


def test_secret_partly_under_the_toast_keeps_only_its_visible_ink():
    # search seeds rather than pin one, so layout changes don't silently drop the case
    for seed in range(1, 60):
        session = editor_session(seed=seed, n_frames=12, size=(640, 360))
        partial = [s for f in session for s in f.secrets if s.kind != "slack_bot" and 0 < s.ink.mean() < 0.1]
        if partial:
            break
    assert partial, "no seed puts an editor secret mostly under the toast"
    assert score(session, [oracle_mask(f) for f in session]).leaked_frames == 0


def test_runner_reports_latency_and_throughput():
    session = editor_session(seed=7, n_frames=8, size=(640, 360))
    result = timed_run(session, oracle_mask)
    assert len(result.latencies_ms) == 8 and result.p50_ms > 0 and result.fps > 0
    assert result.score.leaked_frames == 0


def test_gate_with_a_perfect_detector_leaks_nothing_on_either_scenario():
    for make in SESSIONS.values():
        session = make(seed=7, n_frames=12)
        size = session[0].pixels.shape[1::-1]
        result = timed_run(session, gate_pipeline(lambda f: [s.rect for s in f.secrets], size))
        assert result.score.leaked_frames == 0 and result.score.mean_false_mask == 0


def test_blank_ink_never_counts_as_a_leak():
    blank = np.full((10, 10, 3), 30, dtype=np.uint8)
    assert surviving_ink(blank, blank, np.zeros((10, 10), dtype=bool)) == 0.0
