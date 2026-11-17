from __future__ import annotations

import numpy as np


def _lateral(points: np.ndarray, incoming: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """Seitlicher Abstand von der Verlängerung des Linienstücks (+ = rechts)."""
    origin, direction = incoming
    # Bewusst elementweise statt mit @: numpy-Matrixprodukt meldet auf macOS (Accelerate)
    # bei großen Arrays falsche "divide by zero"-Warnungen.
    return (points[:, 0] - origin[0]) * -direction[1] + (points[:, 1] - origin[1]) * direction[0]
