"""Capture one window with Windows.Graphics.Capture (the windows-capture package)."""

import threading
import time

import numpy as np
from windows_capture import WindowsCapture

from engine.gate import RawFrame


class WindowSource:
    """Keeps only the newest frame: a slow gate skips frames instead of queueing them."""

    def __init__(self, window_name: str, max_fps: int = 10):
        self.closed = False
        self._latest: RawFrame | None = None
        self._next_id = 0
        self._arrived = threading.Condition()
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_name=window_name,
            minimum_update_interval=1000 // max_fps,
        )

        @capture.event
        def on_frame_arrived(frame, control):
            # frame_buffer maps a GPU texture that is only valid inside this
            # callback: copy it out now (BGRA -> RGB), never keep the view
            pixels = np.ascontiguousarray(frame.frame_buffer[:, :, 2::-1])
            with self._arrived:
                self._latest = RawFrame(self._next_id, time.monotonic(), pixels)
                self._next_id += 1
                self._arrived.notify()

        @capture.event
        def on_closed():
            with self._arrived:
                self.closed = True
                self._arrived.notify()

        self._control = capture.start_free_threaded()

    def next_frame(self, timeout: float = 1.0) -> RawFrame | None:
        """The newest frame not handed out yet; None on timeout or once closed."""
        with self._arrived:
            self._arrived.wait_for(lambda: self._latest is not None or self.closed, timeout)
            frame, self._latest = self._latest, None
            return frame

    def repeat(self, frame: RawFrame) -> RawFrame:
        """The same pixels as a new capture, stamped now.

        Honest only because WGC delivers a frame whenever the window changes:
        if none arrived since `frame`, its pixels are still what is on screen.
        Lets a frame that missed its deadline be retried once the screen settles,
        instead of freezing until the next change.
        """
        with self._arrived:
            repeated = RawFrame(self._next_id, time.monotonic(), frame.pixels)
            self._next_id += 1
            return repeated

    def stop(self) -> None:
        self._control.stop()
