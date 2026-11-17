from __future__ import annotations

import cv2
import numpy as np


def preprocess(frame_rgb: np.ndarray, rotate: int, flip: bool = False) -> np.ndarray:
    """Bild drehen/spiegeln (Kameraausrichtung, siehe calibrate.py) und in Graustufen umwandeln."""
    rotations = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    if rotate in rotations:
        frame_rgb = cv2.rotate(frame_rgb, rotations[rotate])
    if flip:
        frame_rgb = cv2.flip(frame_rgb, 1)
    return cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
