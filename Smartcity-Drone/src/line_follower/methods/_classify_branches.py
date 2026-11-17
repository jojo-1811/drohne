from __future__ import annotations

from typing import Optional

import numpy as np

from src.line_follower.methods._lateral import _lateral


def _classify_branches(branches: list[tuple[float, np.ndarray]],
                       incoming: Optional[tuple[np.ndarray, np.ndarray]], w: int) -> tuple[tuple[float, int], ...]:
    """Jeden Ast als links / geradeaus / rechts einordnen.

    Maßstab ist die Verlängerung des Linienstücks, auf dem die Drohne gerade
    fliegt. Ein Ast, dessen Austrittspunkt nahe an dieser Verlängerung liegt,
    führt geradeaus weiter; sonst zählt, auf welcher Seite er liegt. Die Richtung
    von der Bildmitte aus taugt dafür nicht: Liegt eine Kreuzung noch weit vorn,
    verlassen auch ihre Seitenäste das Bild am oberen Rand fast "geradeaus".
    """
    if len(branches) < 2 or incoming is None:
        return tuple((bearing, 0) for bearing, _ in branches)
    classified = []
    for bearing, point in branches:
        lateral = float(_lateral(point[None, :], incoming)[0])
        classified.append((bearing, 0 if abs(lateral) < w / 8 else int(np.sign(lateral))))
    return tuple(classified)
