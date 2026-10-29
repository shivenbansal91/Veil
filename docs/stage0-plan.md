# Stage 0 — Browser hook spike (plan)

**Question:** can a Chrome MV3 extension wrap `getDisplayMedia` so that the page (and Google Meet's receivers) only ever get frames that went through our processing, in every case we can think of?

**Answer format:** go / no-go, written up in `experiments/<date>-stage0.md`. No-go is a fine result: the browser route gets shelved and the protected window stays the only route.

**Timebox:** about 3 working days. If it's still unclear after that, the answer is no-go for V1.

## Independence from the main route

- Lives only in `extensions/chrome/stage0/`. Plain JS for the spike (no build step); TypeScript only if the route passes.
- Does not import from `engine/`, and nothing in `engine/` or `bench/` depends on it.
- Stage 1 (VEIL-Bench) and Stage 2 (protected window) go ahead whatever Stage 0 finds. Nothing in them waits for this result or is designed around it.
- No frame processing beyond a test pattern. Sending frames to the engine is a later problem, and only if this passes.

## Setup

**Extension** (unpacked, Chrome 156 is installed):
- `manifest.json`: MV3, content script with `"world": "MAIN"`, `"run_at": "document_start"`, `"all_frames": true`, `"match_origin_as_fallback": true`, `"match_about_blank": true`. Matches: the local test page and `https://meet.google.com/*`.
- Wraps `MediaDevices.prototype.getDisplayMedia` (the prototype, not just the `navigator.mediaDevices` instance).
- Wrapper: call the original, pipe the video track through `MediaStreamTrackProcessor` → transform → `MediaStreamTrackGenerator`, return a stream holding the generated track.

**Fail-closed rules inside the wrapper** (these are part of what's tested):
- If anything throws while building the pipeline: stop the original tracks and reject the promise. Never return the original stream.
- If the per-frame transform throws: enqueue a black frame (or nothing), close the original `VideoFrame`. Never enqueue the original.
- Stopping either track stops the other.

**Test pattern (pixel-based, no OCR):**
- The *shared content* is a local `canary.html`: solid pure green (`#00FF00`) with a moving frame counter, full screen when sharing a screen.
- The *processed output* is solid magenta (`#FF00FF`) plus our own frame counter.
- So any green in what the receiver sees means an unmodified frame got through. Classify per pixel with a margin for video compression (e.g. green if G > 180 and R, B < 100), and count frames with any green blob larger than a few pixels.

**Receiver side:**
- Local page: the page itself draws the received track to a `<canvas>` and counts green frames live.
- Meet: second device in the call (other Google account) records its screen for the whole run (OBS / Win+Alt+R on a laptop, screen recorder on the phone). A small script then counts green frames in the recording. That script is non-core; the assistant can write it.

## Test cases

Each case runs at least 3 times. Log for each: unmodified frames seen, time to first frame, anything odd.

**Local test page**

| ID | Case |
|---|---|
| L1 | Basic share: tab, window, entire screen |
| L2 | Startup: are the very first frames processed? (Record from the moment the picker closes) |
| L3 | Transform throws: on every frame, on every 10th frame, on the first frame |
| L4 | Pipeline setup throws (forced): does the page get a rejection, never a raw stream? |
| L5 | Stop via Chrome's "Stop sharing" bar, via `track.stop()` in the page, then share again |
| L6 | "Share this tab instead" (switching the surface mid-share) |
| L7 | Calling `getDisplayMedia` from a same-origin iframe and from a fresh `about:blank` iframe's realm (an easy way to reach an unwrapped prototype) |
| L8 | Track metadata the page might check: `getSettings().displaySurface`, `label`, `onended`, `contentHint`, audio track when sharing a tab with audio |

**Google Meet (controlled call, own two accounts)**

| ID | Case |
|---|---|
| M1 | Present tab / window / entire screen, receiver recording |
| M2 | Stop presenting, start again, 3 times |
| M3 | Reconnection: turn Wi-Fi off on the sender for ~10 s, then back on |
| M4 | Transform-throws mode (flag in the extension) during a live share |
| M5 | Does Meet still treat it as a presentation (layout, receiver UI) or does something break or degrade? |
| M6 | Disable/reload the extension mid-share: what happens to the running share? Observe and record it, no pass/fail |

## Pass / fail

**Pass** requires all of:
- Zero green frames at the receiver in every L and M case (M6 excepted, observation only).
- The wrapper never returns or forwards an original track in any error case (L3, L4).
- Meet keeps working well enough to present (M5).

**Any single green frame in any case = no-go for V1.** Write down which case caused it; it's useful later.

## Known risks to record honestly in the write-up

- **Fail-open by absence.** The wrapper can be fail-closed only where it actually runs. If a Chrome update, a Meet change, or a frame we don't inject into reaches the real `getDisplayMedia`, the share goes out raw *and nothing tells us*. The protected window doesn't have this problem, because Meet only ever sees our window. This is the biggest structural argument against the browser route even if every test passes.
- `MediaStreamTrackGenerator` is Chrome-specific; the standard `VideoTrackGenerator` is specified for workers. Which one works on the main thread in Chrome 156 is **unverified**. Check it first.
- Meet may change any time; a pass is a snapshot of one Chrome version and one Meet build. Record both versions.
