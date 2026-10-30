"""The protected window: the window the user shares in the meeting.

It paints whatever `publish()` returns and has no other source of pixels, so
it can only ever show approved, frozen or blackout frames (CLAUDE.md §5.1).
"""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

from engine.gate import OutgoingFrame

TITLE = "VEIL protected view"
REPAINT_MS = 66  # ~15 fps; Meet sends screen shares at roughly 5-15 fps


class ProtectedWindow(QWidget):
    def __init__(self, publish: Callable[[], OutgoingFrame | None]):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(1280, 720)
        self._publish = publish
        self._image: QImage | None = None
        self._timer = QTimer(self, interval=REPAINT_MS, timeout=self._refresh)
        self._timer.start()

    def _refresh(self) -> None:
        # None before the first frame and after the engine stops: stay black
        self._frame = frame = self._publish()  # held so the pixels outlive the QImage view
        if frame is None:
            self._image = None
        else:
            h, w, _ = frame.pixels.shape
            self._image = QImage(frame.pixels.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self._image is not None:
            scaled = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x, y = (self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2
            painter.drawImage(self.rect().adjusted(x, y, -x, -y), self._image)
        painter.end()
