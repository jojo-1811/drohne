from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class LineMeasurement:
    """Lage der Linie relativ zur Drohne (= Bildmitte, oben = vorne)."""

    offset: float  # Seitenversatz der Linie auf Höhe der Drohne: -1 (ganz links) .. +1 (ganz rechts)
    angle: float  # Richtung zum Zielpunkt in Grad, 0 = geradeaus, + = rechts, ±90 = Ecke
    contour: np.ndarray
    anchor: tuple[int, int]  # Linienpunkt auf Höhe der Drohne
    target: tuple[int, int]  # Stelle, an der die Linie in Flugrichtung weiterläuft
    # Äste vor der Drohne als (Richtung in Grad, Seite: -1 links / 0 geradeaus / +1 rechts); ≥ 2 = Abzweigung
    branches: tuple[tuple[float, int], ...] = ()
    # Stelle, an der ein Seitenast auf das Linienstück der Drohne trifft (Kreuzung, Ecke), sonst None
    junction: Optional[tuple[int, int]] = None
