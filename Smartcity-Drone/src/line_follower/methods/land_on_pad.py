from __future__ import annotations

import logging

import numpy as np
from djitellopy import Tello, TelloException

from src.line_follower.methods._brake import _brake
from src.line_follower.utils import PAD_TOLERANCE_CM, SUCCESS, log


def land_on_pad(tello: Tello, pad_id: int, height_cm: int, attempts: int = 3,
                logger: logging.Logger = log) -> None:
    """Abbremsen, genau über der Padmitte positionieren und landen.

    Die Tello erkennt das Pad schon, wenn es noch vorn im Bild liegt, und die Drohne
    fliegt dann noch mit Schwung (Flug 26.10. 12:04: Pad bei der Auslösung etwa eine
    Viertel Bildhöhe voraus, fb 20). Deshalb erst stillstehen, dann mit dem go-Befehl
    relativ zum Pad über die Mitte fliegen und die Lage nachmessen; liegt sie mehr als
    PAD_TOLERANCE_CM daneben, erneut positionieren.
    """
    _brake(tello)
    centered = False
    for attempt in range(1, attempts + 1):
        if tello.get_mission_pad_id() != pad_id:
            logger.warning(f"Pad m{pad_id} nicht mehr im Blick – lande hier.")
            break
        try:
            tello.go_xyz_speed_mid(0, 0, height_cm, 20, pad_id)
        except TelloException as error:
            logger.warning(f"Positionieren über Pad m{pad_id} fehlgeschlagen ({error}) – lande hier.")
            break
        _brake(tello)
        x, y = tello.get_mission_pad_distance_x(), tello.get_mission_pad_distance_y()
        logger.info(f"Über Pad m{pad_id}: {x:+d} cm / {y:+d} cm neben der Mitte (Versuch {attempt}/{attempts}).")
        if np.hypot(x, y) <= PAD_TOLERANCE_CM:
            centered = True
            break
    tello.land()
    if centered:
        logger.log(SUCCESS, f"Auf Pad m{pad_id} gelandet.")
    else:
        logger.warning(f"Gelandet, aber nicht genau über Pad m{pad_id}.")
