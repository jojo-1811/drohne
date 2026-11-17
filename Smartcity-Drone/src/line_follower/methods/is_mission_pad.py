from __future__ import annotations

import cv2
import numpy as np


def is_mission_pad(gray: np.ndarray, region: np.ndarray, min_dots: int = 3) -> bool:
    """Ist das dunkle Objekt ein Mission Pad? Erkennbar an seinen runden hellen Punkten.

    Gemessen in den Flügen vom 20.10. und 26.10.: Pads zeigen 3–10 runde helle
    Flecken, die Roboter höchstens einen (ihre Marker sind eckig).
    """
    values = gray[region].reshape(-1, 1)
    threshold, _ = cv2.threshold(values, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = ((gray > threshold) & region).astype(np.uint8) * 255
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    dots = 0
    for contour in contours:
        area, perimeter = cv2.contourArea(contour), cv2.arcLength(contour, True)
        if 0.0004 * gray.size <= area <= 0.01 * gray.size and 4 * np.pi * area / perimeter ** 2 > 0.7:
            dots += 1
    return dots >= min_dots
