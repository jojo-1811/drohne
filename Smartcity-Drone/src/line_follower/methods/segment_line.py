from __future__ import annotations

from typing import Optional

import cv2
import numpy as np


def segment_line(gray: np.ndarray, line_is_dark: bool, threshold: Optional[int], contrast: int = 25) -> np.ndarray:
    """Binärmaske der Linie: Linie = 255, Hintergrund = 0.

    Ohne festen Schwellwert wird jedes Pixel mit der mittleren Helligkeit seiner
    Umgebung verglichen (adaptiver Schwellwert). Das ist robust gegen Lichtflecken
    und Helligkeitsverläufe – anders als ein globaler Schwellwert (Otsu), der bei
    einer schmalen Linie eher "Lichtfleck vs. Rest" statt "Linie vs. Boden" trennt.
    """
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    mode = cv2.THRESH_BINARY_INV if line_is_dark else cv2.THRESH_BINARY
    if threshold is None:
        # Umgebung deutlich breiter als die Linie wählen, sonst wird ihre Mitte nicht erfasst.
        block_size = int(gray.shape[1] * 0.3) | 1
        offset = contrast if line_is_dark else -contrast
        mask = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C, mode, block_size, offset)
    else:
        _, mask = cv2.threshold(blurred, threshold, 255, mode)
    # Klein genug für dünnes Band (1,5 cm ≈ 6 px bei 50 cm Höhe) – ein größerer Kern
    # würde dessen Striche beim Öffnen in Bruchstücke zerlegen.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
