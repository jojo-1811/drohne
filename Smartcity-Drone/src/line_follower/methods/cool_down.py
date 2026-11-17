from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from djitellopy import Tello, TelloException

from src.line_follower.classes.flight_error import FlightError
from src.line_follower.utils import SUCCESS, log


def cool_down(tello: Tello, target_temp: float, timeout_s: float = 300, logger: logging.Logger = log,
              stop: Optional[Callable[[], bool]] = None,
              on_update: Optional[Callable[[float, float], None]] = None) -> bool:
    """Kühlmodus der Tello (motoron): Propeller drehen langsam, die Drohne hebt nicht ab.

    Am Boden fehlt der Luftstrom der Propeller, deshalb heizt sich die Drohne dort
    auf. Läuft, bis target_temp erreicht ist, timeout_s vergangen ist oder stop()
    True liefert. on_update(Sekunden, °C) meldet den Verlauf (z. B. ans Webinterface).
    Liefert True, wenn die Zieltemperatur erreicht wurde.
    """
    stop = stop or (lambda: False)
    logger.warning("Kühlmodus: Die Propeller drehen sich gleich (Drohne hebt nicht ab). "
                   "Hände weg von den Propellern, Drohne flach auf freiem Boden.")
    for remaining in (5, 4, 3, 2, 1):
        logger.info(f"Kühlmodus startet in {remaining} s …")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if stop():
                logger.info("Kühlen abgebrochen.")
                return False
            time.sleep(0.1)
    # Bei eingeschalteter Mission-Pad-Erkennung ("mon", vom Flug zuvor) beantwortet die Drohne
    # "motoron" nur mit "error" – nach "moff" sofort "ok" (gemessen 2.11. an der 108).
    try:
        tello.disable_mission_pads()
        tello.turn_motor_on()
    except TelloException as error:
        raise FlightError(f"Drohne lehnt den Kühlmodus ab ({error}). Drohne flach und ruhig hinlegen und erneut "
                          "versuchen – sonst ausschalten und abkühlen lassen.") from error
    start = time.monotonic()
    last_command = start
    try:
        while time.monotonic() - start < timeout_s:
            elapsed, temperature = time.monotonic() - start, tello.get_temperature()
            if on_update is not None:
                on_update(elapsed, temperature)
            if temperature <= target_temp:
                logger.log(SUCCESS, f"Abgekühlt auf {temperature:.0f} °C nach {elapsed:.0f} s.")
                return True
            if stop():
                logger.info(f"Kühlen abgebrochen bei {temperature:.0f} °C.")
                return False
            if time.monotonic() - last_command >= 5:
                # Ein Befehl alle paar Sekunden – sonst greift die 15-s-Sicherheitsabschaltung.
                tello.query_battery()
                logger.info(f"Kühlt: {temperature:.0f} °C (Ziel ≤ {target_temp:.0f} °C)")
                last_command = time.monotonic()
            time.sleep(0.2)
        logger.warning(f"Kühlen nach {timeout_s:.0f} s beendet, noch {tello.get_temperature():.0f} °C.")
        return False
    finally:
        tello.turn_motor_off()
