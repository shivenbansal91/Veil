"""A window that plays a VEIL-Bench editor session, so the planted (fake) secrets are known.

Run: .venv\\Scripts\\python experiments\\2026-10-30-stage2-smoke\\source.py [seed]
"""

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bench.scenario import editor_session

FRAME_MS = 500


class Source(QWidget):
    def __init__(self, seed: int):
        super().__init__()
        self.setWindowTitle("VEIL test source")
        self._frames = editor_session(seed=seed, n_frames=30, size=(1280, 720))
        self._i = 0
        self.resize(1280, 720)  # resizable, for the Meet test's resize run
        QTimer(self, interval=FRAME_MS, timeout=self._advance).start()

    def _advance(self):
        prev, self._i = self._i, (self._i + 1) % len(self._frames)
        # repaint only on change, like a real editor: WGC then delivers no frame
        # while the screen is still, which is the case the engine's retry handles
        if not (self._frames[prev].pixels == self._frames[self._i].pixels).all():
            self.update()

    def paintEvent(self, event):
        px = self._frames[self._i].pixels
        painter = QPainter(self)
        painter.drawImage(
            0, 0, QImage(px.data, px.shape[1], px.shape[0], 3 * px.shape[1], QImage.Format.Format_RGB888)
        )
        painter.end()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Source(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
    window.show()
    app.exec()
