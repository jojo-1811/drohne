from __future__ import annotations

from typing import Optional, Protocol

import cv2
import numpy as np

from src.line_follower.classes.flight_state import FlightState
from src.line_follower.utils import _read_key


class Frontend(Protocol):
    """Anzeige und Tasteneingabe eines Flugs: OpenCV-Fenster (CLI) oder Browser (webapp.py)."""

    def show(self, view: Optional[np.ndarray], state: FlightState) -> None:
        """Neues Bild (None vor dem ersten Kamerabild) und Zustand anzeigen."""

    def poll_key(self, wait_ms: int) -> Optional[str]:
        """Höchstens wait_ms auf eine Taste warten: q = landen, a/w/d/l an Abzweigungen, f / flip-* = Flip."""


class WindowFrontend:
    """Videofenster von OpenCV; Tasten nur, wenn das Fenster aktiv ist."""

    def show(self, view: Optional[np.ndarray], state: FlightState) -> None:
        if view is not None:
            cv2.imshow("Tello Line Follower", view)

    def poll_key(self, wait_ms: int) -> Optional[str]:
        return _read_key(wait_ms)

    def close(self) -> None:
        cv2.destroyAllWindows()
