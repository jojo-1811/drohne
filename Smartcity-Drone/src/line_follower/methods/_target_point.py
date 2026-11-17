from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from src.line_follower.methods._branches import _branches
from src.line_follower.methods._is_line_end import _is_line_end
from src.line_follower.utils import KEEP_MAX_JUMP, MAX_END_BEARING


def _target_point(contour: np.ndarray, xs: np.ndarray, ys: np.ndarray, w: int, h: int,
                  center: np.ndarray, anchor: np.ndarray,
                  previous_bearing: Optional[float] = None,
                  keep_direction: bool = False) -> tuple[np.ndarray, float, list[tuple[float, np.ndarray]]]:
    """Wo läuft die Linie in Flugrichtung weiter?

    Liefert Zielpunkt, dessen Richtung (Grad, 0 = vorne) und je Ast, der vor der
    Drohne abgeht, Richtung und Austrittspunkt (siehe _branches).

    Kandidaten sind alle Stellen, an denen die Linie aufhört: Austritte am oberen,
    linken und rechten Bildrand sowie Linienenden im Bild. Gewählt wird der
    Kandidat, der am wenigsten von "geradeaus" abweicht. Mit previous_bearing
    (Richtung des Ziels im letzten Bild) zählt zusätzlich die Abweichung davon:
    An einem T-Stück liegen beide Äste etwa gleich weit von geradeaus weg, ohne
    diese Regel würde die Ziel-Wahl von Bild zu Bild zwischen links und rechts
    springen. An einer Kreuzung bleibt geradeaus vorn, weil die vorherige
    Richtung dort ebenfalls geradeaus war.

    Mit keep_direction zählt nur die Abweichung von previous_bearing: So bleibt
    die Drohne auf dem Ast, den der Pilot an einer Abzweigung gewählt hat, auch
    wenn geradeaus ein anderer Ast weiterläuft.

    Linienenden im Bild sind nötig, weil sich die Drohne an einer Ecke dreht: Das
    Stück, über das sie gekommen ist, wandert dann vom unteren an den seitlichen
    Bildrand, während das neue Stück oft knapp vor dem Rand endet (Vignettierung
    der Kamera). Nur mit Randaustritten würde sie zurück statt weiter fliegen.
    """
    margin = 2
    on_edge = ((ys <= margin) | (xs <= margin) | (xs >= w - 1 - margin)) & (ys < h - 1 - margin)
    line_mask = np.zeros((h, w), np.uint8)
    line_mask[ys, xs] = 255
    width = 2.0 * float(cv2.distanceTransform(line_mask, cv2.DIST_L2, 5).max())
    hull = cv2.convexHull(contour).reshape(-1, 2)
    hull = hull[hull[:, 1] < h - 1 - margin]
    # Ein Linienende hinter der Drohne (Bandanfang, verdeckte Linie) ist nie das Ziel – von dort kommt sie.
    ahead = np.abs(np.degrees(np.arctan2(hull[:, 0] - center[0], center[1] - hull[:, 1]))) <= MAX_END_BEARING
    ends = np.array([p for p in hull[ahead] if _is_line_end(line_mask, p, radius=1.5 * width)]).reshape(-1, 2)
    points = np.vstack([np.column_stack([xs[on_edge], ys[on_edge]]), ends]).astype(float)
    # Punkte direkt an der Drohne (z. B. Außenkante einer Ecke) sagen nichts über die Richtung.
    # Nicht größer wählen: Endet die Linie geradeaus kurz vor einem Pad, fiele dieses Ende
    # sonst weg und an einer Kreuzung davor würde die Abzweigung gewählt.
    points = points[np.hypot(*(points - anchor).T) >= h / 8]
    def keep_previous() -> tuple[np.ndarray, float, list[tuple[float, np.ndarray]]]:
        keep = previous_bearing if previous_bearing is not None else 0.0
        return anchor + np.array([np.sin(np.radians(keep)), -np.cos(np.radians(keep))]), keep, []

    if len(points) == 0:
        # Kein Kandidat (Linie endet hier oder der gewählte Ast liegt gerade direkt an der
        # Drohne): bisherige Richtung beibehalten. "Geradeaus" würde mitten in einer
        # Drehung die Richtung umkehren.
        return keep_previous()
    bearing = np.degrees(np.arctan2(points[:, 0] - center[0], center[1] - points[:, 1]))
    ahead = points[:, 1] <= anchor[1] - h / 8
    branches = _branches(bearing[ahead], points[ahead])
    if previous_bearing is None:
        cost = np.abs(bearing)
    elif keep_direction:
        cost = np.abs((bearing - previous_bearing + 180) % 360 - 180)  # Winkelabstand mit Überlauf bei ±180°
        if cost.min() > KEEP_MAX_JUMP:
            # Der gewählte Ast ist gerade nicht als Kandidat zu sehen; der nächstbeste liegt in ganz
            # anderer Richtung – meist das andere Ende derselben Linie. Flug 2.11. 10:18: Nach "links"
            # an einem T-Stück drehte die Drohne wegen der Verzögerung erst noch nach rechts weiter,
            # der linke Ast wanderte nach hinten (-127°), das Ziel sprang auf +51° und die Drohne
            # drehte zurück, bis die Linie aus dem Bild war. Richtung halten, bis der Ast wieder da ist.
            return keep_previous()
    else:
        cost = np.abs(bearing) + np.abs(bearing - previous_bearing)
    best = np.argmin(cost)
    # Auf die Linienmitte an dieser Stelle ziehen.
    near = np.hypot(xs - points[best, 0], ys - points[best, 1]) <= h / 12
    return np.array([xs[near].mean(), ys[near].mean()]), float(bearing[best]), branches
