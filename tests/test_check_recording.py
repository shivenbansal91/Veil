import cv2
import numpy as np

from bench.check_recording import changed_frames, readable_secrets


def test_a_one_frame_flash_in_one_line_is_kept(tmp_path):
    path = tmp_path / "flash.avi"
    video = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (640, 360))
    still = np.full((360, 640, 3), 30, dtype=np.uint8)
    flash = still.copy()
    flash[200:214, 100:400] = 220  # one text line, one frame
    for frame in [still] * 10 + [flash] + [still] * 10:
        video.write(frame)
    video.release()

    kept = list(changed_frames(path))
    assert len(kept) == 3  # first frame, the flash, the return to still
    assert abs(kept[1][0] - 10 / 30) < 1e-6
    assert kept[1][1][207, 250].mean() > 150


def test_compression_noise_alone_is_not_a_change(tmp_path):
    path = tmp_path / "noise.avi"
    video = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (640, 360))
    rng = np.random.default_rng(0)
    for _ in range(20):
        video.write(np.clip(30 + rng.integers(-4, 5, (360, 640, 3)), 0, 255).astype(np.uint8))
    video.release()
    assert len(list(changed_frames(path))) == 1


def test_readable_means_ten_consecutive_characters():
    key = "sk_" + "live_" + "Q7wE9rT1yU3iO5pA2sD4fG6h"  # built in pieces so gitleaks doesn't flag the fake
    planted = {"stripe": key}
    assert readable_secrets("STRIPE_SECRET_KEY=" + key[:19], planted) == {"stripe": key[:19]}
    assert readable_secrets("STRIPE_SECRET_KEY=" + key[:10], planted) == {"stripe": key[:10]}  # exactly 10
    assert readable_secrets("STRIPE_SECRET_KEY=" + key[:9], planted) == {}
