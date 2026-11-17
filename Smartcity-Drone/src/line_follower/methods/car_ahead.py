from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from src.line_follower.classes.line_measurement import LineMeasurement
from src.line_follower.utils import CAR_MIN_HIT, _int_point


def car_ahead(cars: np.ndarray, line: LineMeasurement) -> Optional[float]:
    """Abstand des nächsten Autos auf der Strecke vor der Drohne (Anteil der Bildhöhe), sonst None.

    "Auf der Strecke" heißt: im Korridor (ein Drittel der Bildbreite breit) vom Anker
    zum Ziel und ein Stück darüber hinaus – steht das Auto auf dem Band, endet die
    erkannte Linie davor. Autos neben der Strecke halten die Drohne nicht auf.
    """
    h, w = cars.shape
    anchor, target = np.array(line.anchor, float), np.array(line.target, float)
    direction = target - anchor
    length = float(np.hypot(*direction))
    direction = direction / length if length > 0 else np.array([0.0, -1.0])
    end = target + direction * h / 4
    corridor = np.zeros_like(cars)
    cv2.line(corridor, _int_point(anchor), _int_point(end), 255, max(1, w // 3))
    ys, xs = np.nonzero(cars & corridor)
    ahead = (xs - anchor[0]) * direction[0] + (ys - anchor[1]) * direction[1]
    ahead = ahead[ahead > 0]  # Autos neben oder hinter der Drohne sind nicht im Weg
    if len(ahead) < CAR_MIN_HIT * h * w:
        return None
    return float(ahead.min()) / h
