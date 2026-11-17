from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from src.line_follower.classes.line_measurement import LineMeasurement


def draw_overlay(gray: np.ndarray, mask: np.ndarray, line: Optional[LineMeasurement], status: str,
                 prompt: str = "", cars: Optional[np.ndarray] = None) -> np.ndarray:
    view = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    h, w = gray.shape
    if cars is not None and cars.any():
        contours, _ = cv2.findContours(cars, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(view, contours, -1, (255, 0, 255), 2)
    cv2.line(view, (w // 2, 0), (w // 2, h), (255, 255, 0), 1)
    if line is not None:
        cv2.drawContours(view, [line.contour], -1, (0, 255, 0), 2)
        cv2.circle(view, line.anchor, 5, (0, 0, 255), -1)
        cv2.arrowedLine(view, line.anchor, line.target, (0, 0, 255), 2)
        status += f" | offset {line.offset:+.2f} angle {line.angle:+.0f}"
    cv2.putText(view, status, (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    if prompt:
        cv2.putText(view, prompt, (5, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 128, 255), 1)
    small_mask = cv2.resize(cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), (w // 3, h // 3))
    view[h - h // 3 :, w - w // 3 :] = small_mask
    return view
