from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from src.line_follower.methods._lateral import _lateral


def _junction_point(xs: np.ndarray, ys: np.ndarray, incoming: Optional[tuple[np.ndarray, np.ndarray]],
                    w: int, h: int) -> Optional[np.ndarray]:
    """Wo trifft ein Seitenast auf das Linienstück der Drohne?

    Seitenast = Linienpixel deutlich neben der Verlängerung (> w/4). Durch den
    Teil des Astes, der am weitesten seitlich liegt (also fern der Kreuzung und
    damit gerade), wird eine Gerade gelegt; ihr Schnittpunkt mit der Verlängerung
    ist die Kreuzung. Funktioniert auch, wenn die Kreuzung schon auf Höhe der
    Drohne oder dahinter liegt – dann zählt sie nicht mehr als Abzweigung vorn,
    ihre Lage wird aber zum Zurücksetzen gebraucht (siehe JunctionGuide).
    """
    if incoming is None:
        return None
    lateral = _lateral(np.column_stack([xs, ys]).astype(float), incoming)
    origin, direction = incoming
    best = None
    for side in (-1, 1):
        arm = side * lateral > w / 4
        if np.count_nonzero(arm) < 20:
            continue
        ax, ay = xs[arm], ys[arm]
        # Am weitesten außen liegendes Drittel des Astes – dort ist er unbeeinflusst von der Kreuzung.
        outer = side * lateral[arm] >= np.percentile(side * lateral[arm], 66)
        vx, vy, x0, y0 = cv2.fitLine(np.column_stack([ax[outer], ay[outer]]).astype(np.float32),
                                     cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        matrix = np.column_stack([direction, -np.array([vx, vy], float)])
        if abs(np.linalg.det(matrix)) < 0.3:  # Ast fast parallel zur Linie – keine Kreuzung
            continue
        t, _ = np.linalg.solve(matrix, np.array([x0, y0], float) - origin)
        point = origin + t * direction
        # Plausibel nur, wenn der Ast bis nahe an den Schnittpunkt reicht (schlechte
        # Geradenanpassung am Bildrand ergibt sonst Punkte weit außerhalb).
        if np.min(np.hypot(ax - point[0], ay - point[1])) > w / 3:
            continue
        if best is None or point[1] < best[1]:
            best = point
    return best
