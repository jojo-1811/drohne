from __future__ import annotations

import numpy as np


def _is_line_end(line_mask: np.ndarray, point: np.ndarray, radius: float, samples: int = 48) -> bool:
    """Endet die Linie an diesem Punkt (statt dort nur abzuknicken)?

    Auf einem Kreis um den Punkt wird gezählt, wie oft er die Linie schneidet: Am
    Ende eines Streifens ist es ein zusammenhängender Bogen, an einer Ecke oder
    Kurve läuft die Linie in zwei Richtungen weiter (zwei Bögen).
    """
    h, w = line_mask.shape
    phi = np.linspace(0, 2 * np.pi, samples, endpoint=False)
    cx = np.round(point[0] + radius * np.cos(phi)).astype(int)
    cy = np.round(point[1] + radius * np.sin(phi)).astype(int)
    inside = (cx >= 0) & (cx < w) & (cy >= 0) & (cy < h)
    on_line = np.zeros(samples, bool)
    on_line[inside] = line_mask[cy[inside], cx[inside]] > 0
    arcs = int(np.count_nonzero(on_line & ~np.roll(on_line, 1)))
    return arcs == 1
