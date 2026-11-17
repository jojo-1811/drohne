from __future__ import annotations

import cv2
import numpy as np

from src.line_follower.utils import _int_point


def bridge_dashes(mask: np.ndarray, max_gap_ratio: float, max_turn_deg: float = 60.0,
                  min_corner_deg: float = 30.0) -> np.ndarray:
    """Gestrichelte Linie zu einer durchgehenden verbinden.

    Jeder Strich wird als Rechteck mit Mittelpunkt, Richtung und zwei Enden
    beschrieben. Für jedes Ende wird die kürzeste Verbindung zu einem anderen
    Strich gesucht (max_gap = max_gap_ratio × Bildbreite):

    - Gerade weiter: Das andere Ende liegt höchstens max_gap entfernt und
      ungefähr in Verlängerung (Abweichung ≤ max_turn_deg).
    - Ecke: Der andere Strich steht schräg (≥ min_corner_deg) und die
      Verlängerungen beider Striche treffen sich vor beiden Enden. Bei einer
      Ecke aus Strichen beginnt der neue Linienzweig oft seitlich versetzt –
      die direkte Verbindung zeigt dann quer weg und wird von der ersten Regel
      verworfen. Erlaubt sind je Schenkel 1,5 × max_gap, zusammen 2 × max_gap.
      Ein Strich, der die Linie mittig kreuzt, hat kein Ende vor dem
      Schnittpunkt und wird deshalb nicht als Ecke verbunden.

    Die Verbindung wird in Strichbreite eingezeichnet; der Rest der Erkennung
    sieht dann eine normale Linie.
    """
    h, w = mask.shape
    max_gap = max_gap_ratio * w
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    ends = []  # (Strich-Index, Endpunkt, Richtung nach außen, Strichbreite)
    for i, contour in enumerate(contours):
        # Rauschen verwerfen. Die Grenze liegt unter der Fläche eines kurzen Bruchstücks
        # von dünnem Band (spiegelndes Band zerfällt im Licht in Teilstücke).
        if cv2.contourArea(contour) < 0.0008 * h * w:
            continue
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(contour)
        length = max(rw, rh)
        # Strichbreite über die dickste Stelle messen – das umschließende Rechteck wäre
        # bei einem abgeknickten Strich (Ecke) fast quadratisch und viel zu breit.
        filled = np.zeros_like(mask)
        cv2.drawContours(filled, [contour], -1, 255, cv2.FILLED)
        thickness = 2.0 * float(cv2.distanceTransform(filled, cv2.DIST_L2, 5).max())
        rad = np.radians(angle if rw >= rh else angle + 90)
        axis = np.array([np.cos(rad), np.sin(rad)])
        center = np.array([cx, cy])
        for sign in (1, -1):
            ends.append((i, center + sign * axis * length / 2, sign * axis, thickness))

    bridged = mask.copy()
    min_cos = np.cos(np.radians(max_turn_deg))
    min_sin = np.sin(np.radians(min_corner_deg))
    for i, end, outward, thickness in ends:
        best_path, best_cost = None, np.inf
        for j, other, other_outward, _ in ends:
            if j == i:
                continue
            gap = other - end
            dist = float(np.hypot(*gap))
            if 0 < dist <= max_gap and dist < best_cost and (gap / dist) @ outward >= min_cos:
                best_path, best_cost = [end, other], dist
            if abs(outward[0] * other_outward[1] - outward[1] * other_outward[0]) < min_sin:
                continue  # (fast) parallel – keine Ecke
            # Schnittpunkt der Verlängerungen: end + s·outward = other + u·other_outward
            s, u = np.linalg.solve(np.column_stack([outward, -other_outward]), gap)
            tolerance = thickness / 2  # Striche dürfen sich an der Ecke leicht überlappen
            if (-tolerance <= s <= 1.5 * max_gap and -tolerance <= u <= 1.5 * max_gap
                    and s + u <= 2 * max_gap and s + u < best_cost):
                best_path = [end, end + max(s, 0) * outward, other + max(u, 0) * other_outward, other]
                best_cost = s + u
        if best_path is not None:
            points = np.array([_int_point(p) for p in best_path], np.int32)
            cv2.polylines(bridged, [points], False, 255, max(1, int(round(thickness))))
    return bridged
