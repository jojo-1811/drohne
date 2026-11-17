from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from src.line_follower.classes.line_measurement import LineMeasurement
from src.line_follower.classes.line_tracker import LineTracker
from src.line_follower.methods._anchor_point import _anchor_point
from src.line_follower.methods._classify_branches import _classify_branches
from src.line_follower.methods._incoming_line import _incoming_line
from src.line_follower.methods._junction_point import _junction_point
from src.line_follower.methods._target_point import _target_point
from src.line_follower.utils import _int_point


def measure_line(mask: np.ndarray, min_area_ratio: float, max_area_ratio: float,
                 tracker: Optional[LineTracker] = None) -> Optional[LineMeasurement]:
    """Linie im Bild finden und Versatz + Richtung bestimmen.

    Flächen, die mehr als max_area_ratio des Bildes bedecken, sind keine Linie
    (z. B. Schatten, dunkler Boden, gestörtes Bild) und werden verworfen. Mit
    tracker werden zusätzlich linienfremde Objekte aussortiert (siehe LineTracker),
    ohne wird die größte Fläche genommen.

    Die Richtung zeigt vom Linienpunkt auf Höhe der Drohne (Anker) zu der Stelle,
    an der die Linie den Bildrand vorne oder seitlich verlässt (Ziel). Anders als
    eine Ausgleichsgerade über die ganze Linie liefert das an einer 90°-Ecke
    tatsächlich ~90° statt eines Mittelwerts beider Schenkel.
    """
    h, w = mask.shape
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contours = [c for c in contours if min_area_ratio * h * w <= cv2.contourArea(c) <= max_area_ratio * h * w]
    if not contours:
        return None
    contour = tracker.select(mask, contours) if tracker else max(contours, key=cv2.contourArea)
    if contour is None:
        return None

    line_mask = np.zeros_like(mask)
    cv2.drawContours(line_mask, [contour], -1, 255, cv2.FILLED)
    ys, xs = np.nonzero(line_mask)
    center = np.array([w / 2, h / 2])

    anchor = _anchor_point(xs, ys, center, band=max(3, h // 20))
    previous = tracker.target_bearing if tracker else None
    keep = tracker.keep_direction if tracker else False
    target, bearing, branch_points = _target_point(contour, xs, ys, w, h, center, anchor, previous, keep)
    incoming = _incoming_line(xs, ys, anchor, w, h)
    branches = _classify_branches(branch_points, incoming, w)
    junction = _junction_point(xs, ys, incoming, w, h)
    if tracker:
        tracker.target_bearing = bearing

    dx, dy = target - anchor
    angle = float(np.degrees(np.arctan2(dx, -dy)))
    offset = float((anchor[0] - center[0]) / center[0])
    return LineMeasurement(offset, angle, contour, _int_point(anchor), _int_point(target), branches,
                           None if junction is None else _int_point(junction))
