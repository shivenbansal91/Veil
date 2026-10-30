# Stage 2: Google Meet test (plan)

**Question:** does what a real Meet receiver gets ever show a planted secret, or an unprocessed frame, including under faults and when the engine dies?

This is Stage 2's pass condition (CLAUDE.md §13) and the first test that can move "real-time protection" from unvalidated to validated for this setup (principle 9). Everything before it was on this laptop only.

**Write-up:** `experiments/<date>-meet-test/README.md`, with the recordings kept on D: (not in the repo) and the checker output pasted in.

## What you need

- **This laptop** (presenter): signed in to Meet in Chrome.
- **A second device** (receiver), in the call as a different Google account or as an admitted guest.
  - **Preferred: a laptop or desktop.** Join in Chrome, pin the presentation, make it full screen, and record the screen at native resolution: Win+Alt+R (Xbox Game Bar), or OBS at 30 fps.
  - **Phone:** works, but the presentation is small. Record in landscape with the presentation full screen. The positive control (run 0) tells you whether the recording is readable at all.
  - Not a second browser profile on this laptop: it would compete with the engine for the GPU and CPU and change the timing being tested.
- **Nothing real on the shared window.** Only the test source and, in the optional run 5, a generated fake `.env`. The share is window-only, so other windows aren't sent, but don't have real secrets open in the shared windows.
- Put recordings in `D:\Veil-cache\meet-test\` (C: is full). Name them `run0-control.mp4`, `run1-protected.mp4`, `run2-faults.mp4`, `run3-kill.mp4`, `run4-resize.mp4`.

## Before the call (on this laptop, ~3 min)

```bash
bash experiments/2026-10-30-stage2-smoke/run.sh 1 60 --faults
```

Expect `0/5` readable for the protected view, about `4/5` for the raw control, and all four fault kinds in `engine.log`. If not, stop and fix that first.

## Runs

Start the receiver's recording **before** each share starts and stop it after the share ends, so startup and teardown are on the recording. For each run, note the wall-clock time of anything you do (start, kill, resize) so it can be matched to the recording.

The test source loops every 15 s: 5 s still, 5 s scrolling, 5 s still with a Slack toast for 1 s. Its secrets come from seed 1.

**Start the test source** (keep it running for all runs):

```bash
.venv/Scripts/python experiments/2026-10-30-stage2-smoke/source.py 1
```

### Run 0: positive control (unprotected), 30 s

Present now → A window → **VEIL test source**, directly, without VEIL. These are fake secrets, so sending them is fine.

**Expected:** the checker reads most of the planted values (`aws_access_key`, `github_pat`, `stripe_live`, `password`; `slack_bot` if the toast was caught). **If it reads none, the receiver's recording is too low-resolution to validate anything.** Fix that first: full screen the presentation, record at native resolution, use a laptop instead of a phone. Don't go on to runs 1–4 until run 0 reads the secrets.

### Run 1: protected, normal, 2 min

```bash
.venv/Scripts/python -m engine --window "VEIL test source"
```

Present the **VEIL protected view** window as soon as it appears, while it's still black: that's the startup case. Leave it for 2 minutes, which is 8 loops of the source (scrolls, toast). Don't minimise the protected view. It can sit behind other windows.

**Expected:** black at first, then the editor with every secret as a solid black box. During scrolls it freezes on the last approved frame until the scroll ends.

### Run 2: protected, faults injected, 2 min

Stop run 1's engine (close its window), start:

```bash
.venv/Scripts/python -m engine --window "VEIL test source" --faults 2> D:/Veil-cache/meet-test/run2-faults.log
```

Present the protected view again. The schedule fires a crash, a garbage box and two slow frames every 30 analysed frames, and a 7 s hang at analysed frame 25 and then every 100.

**Expected:** short black flashes (crash, garbage), freezes (slow), and about 2 s of black during each hang, from the watchdog after 5 s. Never an unmasked secret. Afterwards, check that the log has all four fault kinds; a run without a hang hasn't tested the watchdog.

### Run 3: kill the engine mid-share, ~30 s

Start the engine normally, present the protected view, wait 15 s, then kill it hard from another terminal:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m engine' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Keep recording for 15 s after the kill.

**This one has no known expected result** (open question in CLAUDE.md §16). The window disappears with the process. Meet may end the presentation, show its last frame, or show something else. It can't show a raw frame, because the window never held one, but write down exactly what the receiver saw and for how long. If Meet keeps showing the last frame indefinitely, that's a product problem to note, not a leak.

### Run 4: resize the source mid-share, ~30 s

Start the engine normally and present the protected view. Then drag the test source window's corner to resize it, three or four times over 30 s.

**Expected:** each size change makes the engine build a fresh gate. The output goes black, then shows the re-read frame at the new size, masked. Locally (three resizes 5 s apart, driven by `MoveWindow`) the view was black from the first resize until about 3 s after the last one, then masked. 0/5 planted values were readable.

### Run 5 (optional): a real app

Generate a fake `.env` and open it in VS Code:

```bash
.venv/Scripts/python -c "import random; from bench.scenario import _fake_values; v=_fake_values(random.Random(99)); open(r'D:\Veil-cache\meet-test\fake.env','w').write(''.join(f'{k.upper()}={x}\n' for k,x in v.items())); open(r'D:\Veil-cache\meet-test\fake-secrets.txt','w').write('\n'.join(v.values()))"
code D:/Veil-cache/meet-test/fake.env
.venv/Scripts/python -m engine --window "fake.env"
```

Present the protected view. Scroll, select text, open and close the file. Score with `--secrets D:\Veil-cache\meet-test\fake-secrets.txt`. VS Code renders differently from the bench (syntax colours, subpixel AA, scaling), so expect this to find things the bench doesn't.

## Scoring

```bash
.venv/Scripts/python -m bench.check_recording D:/Veil-cache/meet-test/run0-control.mp4 --seed 1
.venv/Scripts/python -m bench.check_recording D:/Veil-cache/meet-test/run1-protected.mp4 --seed 1
.venv/Scripts/python -m bench.check_recording D:/Veil-cache/meet-test/run2-faults.mp4 --seed 1
.venv/Scripts/python -m bench.check_recording D:/Veil-cache/meet-test/run3-kill.mp4 --seed 1
.venv/Scripts/python -m bench.check_recording D:/Veil-cache/meet-test/run4-resize.mp4 --seed 1
```

The checker keeps every video frame that changes locally (a one-frame flash in one line is kept), OCRs it, and lists any planted value with ≥10 consecutive characters readable, with its timestamp.

**Also watch the recordings yourself** at the scrolls, the toast, the fault moments (times in the log) and the kill. The checker uses the same OCR model family as the detector, so a secret our OCR can't read is also one the checker can't read. Your eyes are the second check.

## Pass / fail

**Pass, for this setup, if all of these hold:**
- Run 0 reads at least 4 of the 5 planted values, so the method can see a leak.
- Runs 1 and 2: the checker reads **0** planted values, and watching the recording shows no unmasked secret.
- Run 2's log has all four fault kinds, including at least one hang.
- Run 1 starts black, not with source content.
- Runs 3 and 4: no unmasked secret. What Meet showed after the kill is recorded, whatever it was.

**Write it as:** "No readable planted secret detected in the recorded Meet output for these runs", per principle 7. Not "nothing leaked".

**Fail** if any planted value is readable in runs 1–3. Note the timestamp, find the frame, and work out whether the detector missed it (an OCR/rules problem) or a frame got through without the gate (a gate problem; much worse). Then fix it, with a regression test, before re-running.

## What this test doesn't cover

- Any secret format or font beyond the bench's (run 5 is the first step there).
- Display scaling other than this laptop's, multi-monitor setups, and other meeting apps.
- What Meet does with a shared window that changes size (run 4 observes it once).
- Long sessions (hours), GPU contention from other apps, and laptop battery or power-saving modes.
