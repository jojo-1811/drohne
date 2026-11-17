from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from src.line_follower.utils import _stripe_width


class LineTracker:
    """Wählt aus allen dunklen Objekten im Bild dasjenige, das das Klebeband ist.

    Andere dunkle Dinge (z. B. ein Roboter neben der Strecke) erscheinen in der
    Maske als kompakte, breite Klumpen. Das Band dagegen ist ein langer Streifen
    mit gleichbleibender Breite, der sich von Bild zu Bild kaum bewegt. Deshalb:

    1. Streckung: Fläche / Breite² muss mindestens MIN_ELONGATION sein.
    2. Breite: höchstens MAX_WIDTH_FACTOR × der zuletzt gemessenen Bandbreite, mindestens aber
       MIN_WIDTH_MARGIN_PX mehr – die Breite wird nur in 2-px-Schritten gemessen, aus 1 m Höhe
       (Band ~4 px) war schon ein Schritt (4 -> 6 px) über der Grenze (Flug 16.11. 10:28).
    3. Kontinuität: Gibt es mehrere Kandidaten, gewinnt der, der am stärksten mit
       der Linie aus dem vorherigen Bild überlappt.

    Die gemerkte Bandbreite lernt nur aus Streifen, die nicht viel schmaler sind
    (mindestens 1/MAX_WIDTH_FACTOR). Sonst ziehen dünne Schattenkanten – z. B. eines
    vorbeifahrenden Autos – die Breite herunter, bis das echte Band als "zu breit"
    gilt; und weil die Breite nur aus angenommenen Linien lernt, käme sie da nie
    wieder heraus (Flug 16.11.2026 09:41: Linie sichtbar, trotzdem gelandet).
    Als zweite Sicherung wird die Breite vergessen, wenn mehrere Bilder in Folge
    gestreckte Kandidaten nur wegen ihrer Breite verworfen wurden.

    Außerdem merkt er sich die Richtung des letzten Ziels, damit die Drohne an
    einer Abzweigung bei der einmal gewählten Richtung bleibt (siehe _target_point).
    """

    MIN_ELONGATION = 3.5
    MAX_WIDTH_FACTOR = 1.4
    MIN_WIDTH_MARGIN_PX = 2.5
    WIDTH_SMOOTHING = 0.1
    MAX_WIDTH_REJECTS = 6  # Bilder in Folge (~0,5 s), danach Bandbreite neu lernen

    def __init__(self) -> None:
        self.width: Optional[float] = None
        self.target_bearing: Optional[float] = None
        self.keep_direction = False  # nach einer Wahl an der Abzweigung: nur der Richtung folgen
        self._previous: Optional[np.ndarray] = None
        self._width_rejects = 0

    def reset(self) -> None:
        """Nach längerem Linienverlust: Kontinuität vergessen, Bandbreite behalten."""
        self._previous = None
        self.target_bearing = None
        self.keep_direction = False

    def select(self, mask: np.ndarray, contours: list[np.ndarray]) -> Optional[np.ndarray]:
        candidates = []
        too_wide = False
        for contour in contours:
            filled = np.zeros_like(mask)
            cv2.drawContours(filled, [contour], -1, 255, cv2.FILLED)
            width = _stripe_width(filled)
            if cv2.contourArea(contour) / (width * width) < self.MIN_ELONGATION:
                continue
            if self.width is not None and width > max(self.MAX_WIDTH_FACTOR * self.width,
                                                      self.width + self.MIN_WIDTH_MARGIN_PX):
                too_wide = True
                continue
            overlap = int(np.count_nonzero(filled & self._previous)) if self._previous is not None else 0
            candidates.append((overlap, cv2.contourArea(contour), contour, filled, width))
        if not candidates:
            self._width_rejects = self._width_rejects + 1 if too_wide else 0
            if self._width_rejects >= self.MAX_WIDTH_REJECTS:
                self.width = None  # gemerkte Breite passt offenbar nicht mehr
                self._width_rejects = 0
            return None
        self._width_rejects = 0
        _, _, contour, filled, width = max(candidates, key=lambda c: (c[0], c[1]))
        if self.width is None:
            self.width = width
        elif width * self.MAX_WIDTH_FACTOR >= self.width:
            self.width = (1 - self.WIDTH_SMOOTHING) * self.width + self.WIDTH_SMOOTHING * width
        # Etwas aufweiten, damit die Linie auch bei Bewegung zwischen zwei Bildern überlappt.
        grow = max(3, int(self.width))
        self._previous = cv2.dilate(filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow)))
        return contour
