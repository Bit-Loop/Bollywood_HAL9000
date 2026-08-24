"""Delayed click aggregation for the physical speaker drawer."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class SpeakerClickAggregator(QObject):
    doubleClick = Signal()
    tripleClick = Signal()

    def __init__(self, interval_ms: int = 330, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._flush)

    @Slot()
    def registerClick(self) -> None:
        self._count += 1
        if self._count >= 3:
            self._timer.stop()
            self._count = 0
            self.tripleClick.emit()
            return
        self._timer.start()

    @Slot()
    def _flush(self) -> None:
        count = self._count
        self._count = 0
        if count == 2:
            self.doubleClick.emit()
