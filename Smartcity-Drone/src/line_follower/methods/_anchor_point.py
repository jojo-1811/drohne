from __future__ import annotations

import numpy as np


def _anchor_point(xs: np.ndarray, ys: np.ndarray, center: np.ndarray, band: int) -> np.ndarray:
    """Linienposition auf Höhe der Drohne (mittlere Bildzeile), sonst der nächstgelegene Linienpunkt."""
    in_band = np.abs(ys - center[1]) <= band
    if in_band.any():
        row_xs = xs[in_band]
        # Bei mehreren Linienstücken in der Zeile (z. B. an einer Ecke) das der Mitte nächste nehmen.
        nearest = row_xs[np.argmin(np.abs(row_xs - center[0]))]
        segment = row_xs[np.abs(row_xs - nearest) <= band * 10]
        return np.array([segment.mean(), center[1]])
    i = np.argmin((xs - center[0]) ** 2 + (ys - center[1]) ** 2)
    return np.array([xs[i], ys[i]], dtype=float)
