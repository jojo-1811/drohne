from __future__ import annotations

import numpy as np


def _branches(bearings: np.ndarray, points: np.ndarray,
              min_separation: float = 30.0) -> list[tuple[float, np.ndarray]]:
    """Einzelne Äste (Richtung, typischer Austrittspunkt) aus allen Ziel-Kandidaten.

    Kandidaten eines Astes (z. B. alle Pixel seines Austritts am Bildrand) liegen
    dicht beieinander; zwischen zwei Ästen ist eine Lücke von mehr als
    min_separation Grad. Es zählen nur Kandidaten deutlich vor der Drohne (der
    Aufrufer filtert): Nach einer Ecke verlässt das Stück, über das die Drohne
    gekommen ist, das Bild seitlich auf ihrer Höhe – das ist kein neuer Ast.
    """
    if len(bearings) == 0:
        return []
    order = np.argsort(bearings)
    groups = np.split(order, np.nonzero(np.diff(bearings[order]) > min_separation)[0] + 1)
    branches = []
    for group in groups:
        median = float(np.median(bearings[group]))
        branches.append((median, points[group[np.argmin(np.abs(bearings[group] - median))]]))
    return branches
