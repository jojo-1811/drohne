from __future__ import annotations

import logging

from djitellopy import Tello, TelloException

from src.line_follower.utils import log

MIN_STEP_CM = 20  # kleinster Weg, den die Tello mit up/down fliegt (SDK)
TOLERANCE_CM = 5  # so weit darf die Flughöhe vom Ziel abweichen


def go_to_height(tello: Tello, target_cm: int, logger: logging.Logger = log, attempts: int = 2) -> None:
    """Nach dem Abheben auf die gewünschte Flughöhe steigen oder sinken (Höhe laut Abstandssensor).

    Die Tello hebt auf ~80 cm ab und hält danach die Höhe selbst. Kürzere Wege als
    MIN_STEP_CM fliegt sie mit up/down nicht – dann erst MIN_STEP_CM weiter in die
    Gegenrichtung und anschließend um die Summe zurück. Scheitert ein Befehl, fliegt sie in
    der erreichten Höhe weiter – kein Grund, den Flug abzubrechen.
    """
    for _ in range(attempts):
        current = tello.get_distance_tof()
        delta = target_cm - current
        if abs(delta) <= TOLERANCE_CM:
            break
        steps = [delta] if abs(delta) >= MIN_STEP_CM else [-MIN_STEP_CM if delta > 0 else MIN_STEP_CM,
                                                          delta + (MIN_STEP_CM if delta > 0 else -MIN_STEP_CM)]
        try:
            for step in steps:
                (tello.move_up if step > 0 else tello.move_down)(min(abs(step), 500))
        except TelloException as error:
            logger.warning(f"Höhe {target_cm} cm nicht erreicht ({error}) – fliege in {tello.get_distance_tof()} cm weiter.")
            return
    logger.info(f"Flughöhe {tello.get_distance_tof()} cm (Ziel {target_cm} cm).")
