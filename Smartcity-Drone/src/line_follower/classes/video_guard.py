from __future__ import annotations

from collections import deque

import numpy as np


class VideoGuard:
    """Entscheidet, wann der Videostream neu gestartet wird.

    Neustart, wenn in der letzten Sekunde mindestens die Hälfte der Bilder
    zerstört war oder seit STALL_RESTART Sekunden gar kein Bild mehr kam. Ein
    Neustart erzwingt einen neuen Stream, der mit einem vollständigen Bild
    (Keyframe) beginnt. Nach einem Neustart bekommt der Stream RESTART_PAUSE
    Sekunden Zeit, bevor der nächste versucht wird.
    """

    WINDOW = 1.0
    BAD_SHARE = 0.5
    MIN_FRAMES = 5
    STALL_RESTART = 1.0
    RESTART_PAUSE = 3.0

    def __init__(self, max_restarts: int) -> None:
        self.max_restarts = max_restarts
        self.restarts = 0
        self._history: deque[tuple[float, bool]] = deque()
        self._last_restart = -np.inf

    def add(self, corrupt: bool, now: float) -> None:
        self._history.append((now, corrupt))
        while self._history and now - self._history[0][0] > self.WINDOW:
            self._history.popleft()

    def should_restart(self, now: float, last_frame_time: float) -> bool:
        if self.restarts >= self.max_restarts or now - self._last_restart < self.RESTART_PAUSE:
            return False
        if now - last_frame_time > self.STALL_RESTART:
            return True
        bad = sum(corrupt for _, corrupt in self._history)
        return len(self._history) >= self.MIN_FRAMES and bad >= self.BAD_SHARE * len(self._history)

    def restarted(self, now: float) -> None:
        self.restarts += 1
        self._last_restart = now
        self._history.clear()
