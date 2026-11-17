from __future__ import annotations

import logging

from djitellopy import Tello

from src.line_follower.classes.flight_state import FlightState
from src.line_follower.classes.frontend import Frontend
from src.line_follower.methods._cleanup import _cleanup
from src.line_follower.methods.cool_down import cool_down
from src.line_follower.utils import SUCCESS, log


def cool(host: str, target_temp: float, frontend: Frontend, logger: logging.Logger = log) -> None:
    """Drohne am Boden im Kühlmodus abkühlen (siehe cool_down); Taste q bricht ab."""
    state = FlightState(status="KÜHLEN – VERBINDE")
    frontend.show(None, state)
    tello = Tello(host=host)
    try:
        tello.connect()
        state.battery, state.temp_c = tello.get_battery(), tello.get_temperature()
        logger.info(f"Akku: {state.battery} %, Temperatur: {state.temp_c:.0f} °C")
        if state.temp_c <= target_temp:
            logger.log(SUCCESS, f"Schon kühl genug ({state.temp_c:.0f} °C).")
            return

        def update(elapsed: float, temperature: float) -> None:
            state.temp_c, state.battery = temperature, tello.get_battery()
            state.status = f"KÜHLT {temperature:.0f} °C → ≤ {target_temp:.0f} °C ({elapsed:.0f} s)"
            frontend.show(None, state)

        # Läuft noch ein Videostream (z. B. nach einem abgebrochenen Flug), heizt der Encoder weiter:
        # Am 2.11. blieb die 108 nach einem so abgebrochenen Flug trotz Kühlmodus 50 s bei 84 °C
        # (Stream war vermutlich noch an – nicht nachgemessen).
        _cleanup(logger, "Videostream ausschalten", tello.streamoff)
        state.status = "KÜHLEN – PROPELLER STARTEN GLEICH"
        frontend.show(None, state)
        cool_down(tello, target_temp, logger=logger, stop=lambda: frontend.poll_key(0) == "q", on_update=update)
    finally:
        tello.end()
        state.status = "BEENDET"
        frontend.show(None, state)
