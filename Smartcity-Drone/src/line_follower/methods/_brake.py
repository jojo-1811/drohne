from __future__ import annotations

import time

from djitellopy import Tello


def _brake(tello: Tello, timeout_s: float = 2.0) -> None:
    """Schweben, bis die Drohne steht (Geschwindigkeit aus den Statuspaketen, Einheit dm/s)."""
    deadline = time.monotonic() + timeout_s
    still = 0
    while time.monotonic() < deadline and still < 3:
        tello.send_rc_control(0, 0, 0, 0)
        still = still + 1 if tello.get_speed_x() == 0 and tello.get_speed_y() == 0 else 0
        time.sleep(0.1)  # Statuspakete kommen mit ~10 Hz
