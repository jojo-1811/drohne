from __future__ import annotations

from typing import Optional

import cv2
import numpy as np


def _incoming_line(xs: np.ndarray, ys: np.ndarray, anchor: np.ndarray, w: int,
                   h: int) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Gerade durch das Linienstück, auf dem die Drohne fliegt: (Punkt, Richtung nach vorn).

    Genommen werden nur Linienpixel ab kurz vor der Drohne bis zum unteren Bildrand
    und nahe an ihr (±w/8 um den Anker) – sonst zögen Seitenäste einer Kreuzung,
    die gerade auf Höhe der Drohne liegt, die Gerade schräg.
    """
    near = (ys >= anchor[1] - h / 8) & (np.abs(xs - anchor[0]) <= w / 8)
    if np.count_nonzero(near) < 10:
        return None
    vx, vy, x0, y0 = cv2.fitLine(np.column_stack([xs[near], ys[near]]).astype(np.float32),
                                 cv2.DIST_L2, 0, 0.01, 0.01).ravel()
    direction = np.array([vx, vy], float) if vy <= 0 else -np.array([vx, vy], float)
    return np.array([x0, y0], float), direction
