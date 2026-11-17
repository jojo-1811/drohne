from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class WideObjects:
    """Dunkle Flächen, die deutlich breiter sind als ein Klebeband (Mission Pads, Autos)."""

    blobs: np.ndarray  # Kerne dieser Flächen – werden aus der Linienmaske gelöscht
    cars: np.ndarray  # Kerne der Objekte, die keine Mission Pads sind
