from __future__ import annotations

import cv2
import numpy as np

from src.line_follower.classes.wide_objects import WideObjects


def remove_blobs(mask: np.ndarray, objects: WideObjects, max_line_width_ratio: float) -> np.ndarray:
    """Breite Objekte (etwas vergrößert, damit keine Ränder stehen bleiben) aus der
    Linienmaske löschen. Berührt das Band so ein Objekt, wird es dadurch davon getrennt."""
    if not objects.blobs.any():
        return mask
    size = max(3, int(max_line_width_ratio * mask.shape[1])) | 1
    margin = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size // 3 | 1, size // 3 | 1))
    return cv2.bitwise_and(mask, cv2.bitwise_not(cv2.dilate(objects.blobs, margin)))
