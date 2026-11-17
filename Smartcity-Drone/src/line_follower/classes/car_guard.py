from __future__ import annotations

from typing import Optional

import numpy as np


class CarGuard:
    """Hält an, solange ein Auto vorne auf der Strecke steht, und fliegt weiter, wenn es weg ist.

    Ein Auto zählt erst, wenn es CONFIRM_FRAMES Bilder hintereinander im Weg ist
    (einzelne Fehlbilder lösen nichts aus). Weiter geht es erst, wenn CLEAR_AFTER
    Sekunden lang keins mehr im Weg war. Beim Warten hält die Drohne Abstand: Ist
    das Auto näher als KEEP_DISTANCE, setzt sie zurück – nach dem Bremsen gleitet
    sie noch ein Stück weiter (siehe JunctionGuide.hold_forward).
    """

    CONFIRM_FRAMES = 3
    CLEAR_AFTER = 1.0
    KEEP_DISTANCE = 0.25  # Anteil der Bildhöhe zwischen Drohne und Auto
    BACK_GAIN = 60.0  # fb pro Bildhöhe zu wenig Abstand
    BACK_MAX = 20

    def __init__(self) -> None:
        self.since: Optional[float] = None  # Beginn des Wartens
        self.distance: Optional[float] = None
        self._seen = 0
        self._last_seen = -np.inf

    @property
    def waiting(self) -> bool:
        return self.since is not None

    def update(self, distance: Optional[float], now: float, blind: bool = False) -> None:
        """distance: siehe car_ahead. blind: keine Linie im Bild, aber ein Auto – dann steht
        es vermutlich auf der Linie direkt vor oder unter der Drohne; weiter warten."""
        self.distance = distance
        self._seen = self._seen + 1 if distance is not None else 0
        if distance is not None or (blind and self.waiting):
            self._last_seen = now
        if not self.waiting and self._seen >= self.CONFIRM_FRAMES:
            self.since = now
        elif self.waiting and now - self._last_seen > self.CLEAR_AFTER:
            self.since = None

    def hold_forward(self) -> int:
        """Vorwärtsbefehl beim Warten: nie zum Auto hin, zurück, wenn es zu nah ist."""
        if self.distance is None:
            return 0
        return -int(np.clip(self.BACK_GAIN * (self.KEEP_DISTANCE - self.distance), 0, self.BACK_MAX))
