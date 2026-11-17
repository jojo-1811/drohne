from __future__ import annotations

import cv2
import numpy as np

from src.line_follower.classes.wide_objects import WideObjects
from src.line_follower.methods.is_mission_pad import is_mission_pad
from src.line_follower.utils import CAR_MIN_AREA


def find_wide_objects(gray: np.ndarray, line_is_dark: bool, max_line_width_ratio: float,
                      contrast: int = 25) -> WideObjects:
    """Flächen finden, die deutlich breiter sind als ein Klebeband.

    Die breiten Flächen werden nicht in der Linienmaske gesucht, sondern in einer
    eigenen Maske mit großer Vergleichsumgebung (ganze Bildbreite): Der adaptive
    Schwellwert der Linienmaske vergleicht nur mit der näheren Umgebung, im
    Inneren eines Mission Pads ist diese selbst dunkel – das Pad zerfällt dort in
    einen Rand mit Löchern. Die Punkte am Padrand reißen diesen Rand auf, sodass
    auch ein Füllen der Löcher nicht hilft.

    In der Maske der dunklen Bereiche werden Lücken geschlossen und Löcher gefüllt
    (die hellen Punkte des Pads). Ein Öffnen mit einem Kreis von
    max_line_width_ratio × Bildbreite lässt nur Bereiche übrig, in die dieser Kreis
    ganz hineinpasst – das Band ist dafür zu schmal, ein Mission Pad oder ein
    Roboter nicht. Kerne ab CAR_MIN_AREA, deren Objekt kein Mission Pad ist (siehe
    is_mission_pad), gelten als Auto. Bewusst nur die Kerne: Berührt ein Roboter das
    Band, gehört das Band sonst zur Fläche des Roboters, und er reicht scheinbar bis
    unter die Drohne.
    """
    h, w = gray.shape
    size = max(3, int(max_line_width_ratio * w)) | 1
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    mode = cv2.THRESH_BINARY_INV if line_is_dark else cv2.THRESH_BINARY
    offset = contrast if line_is_dark else -contrast
    objects = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C, mode, w | 1, offset)
    gap = size // 2 | 1
    objects = cv2.morphologyEx(objects, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gap, gap)))
    contours, _ = cv2.findContours(objects, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(gray)
    cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
    blobs = cv2.morphologyEx(filled, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    cars = np.zeros_like(gray)
    if blobs.any():
        count, labels = cv2.connectedComponents(filled)
        core_area = np.bincount(labels[blobs > 0], minlength=count)
        for label in np.nonzero(core_area >= CAR_MIN_AREA * h * w)[0]:
            region = labels == label
            if not is_mission_pad(gray, region):
                cars[region & (blobs > 0)] = 255
    return WideObjects(blobs, cars)
