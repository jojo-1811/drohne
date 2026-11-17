from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from djitellopy import Tello

from src.line_follower.utils import log


class DroneGuard:
    """Ausweichen nach oben, wenn eine andere Drohne (oder ein Auto) unter dieser ist.

    Im Kanon fliegen die Drohnen auf verschiedenen Höhen; die verhindern einen Zusammenstoß,
    nicht aber den Abwind. Flug 16.11.2026 11:36: Drohne 109 (90 cm) flog über Drohne 108
    (50 cm), der Abwind drückte die 108 von 53 auf 15–29 cm, sie verlor die Linie und landete.
    In dem Moment maß der Abstandssensor der 109 nur noch 67 statt 90 cm – er traf die 108.

    Deshalb: Fällt der Messwert nach unten mehrere Bilder in Folge um mehr als DROP_CM unter die
    Flughöhe, bleibt die Drohne stehen, steigt auf die Ausweichhöhe und wartet dort, bis der
    Sensor CLEAR_S lang wieder den Boden sieht. Dann sinkt sie zurück und fliegt weiter.
    """

    DROP_CM = 15  # so weit unter der Flughöhe gilt der Messwert als "etwas unter mir"
    CONFIRM = 3  # so viele Messwerte in Folge (~0,2 s), damit Ausreißer nicht auslösen
    CLEAR_S = 1.0  # so lange muss oben wieder der Boden zu sehen sein
    TIMEOUT_S = 20.0  # spätestens dann zurück (z. B. misst der Sensor oben nichts Brauchbares)
    VALID_CM = (10, 300)
    MIN_STEP_CM = 20  # kürzester Weg für up/down (SDK)

    def __init__(self, flight_height_cm: int, guard_height_cm: int) -> None:
        self.flight_height = flight_height_cm
        self.climb = max(self.MIN_STEP_CM, guard_height_cm - flight_height_cm)
        self.guard_height = flight_height_cm + self.climb
        self._count = 0

    def below(self, height_cm: int) -> bool:
        """Neuer Messwert in Flughöhe; True, sobald etwas unter der Drohne ist."""
        valid = self.VALID_CM[0] <= height_cm <= self.VALID_CM[1]
        self._count = self._count + 1 if valid and height_cm < self.flight_height - self.DROP_CM else 0
        return self._count >= self.CONFIRM

    def reset(self) -> None:
        self._count = 0

    def evade(self, tello: Tello, tick: Callable[[str], Optional[str]], logger: logging.Logger = log) -> bool:
        """Steigen, warten bis frei, zurücksinken. tick(status) wird regelmäßig aufgerufen (Anzeige,
        Aufzeichnung) und gibt eine gedrückte Taste zurück. Gibt False zurück, wenn dabei
        "Landen" gedrückt wurde – die Drohne bleibt dann oben, der Aufrufer landet."""
        tello.send_rc_control(0, 0, 0, 0)
        tick(f"DROHNE UNTER MIR – STEIGE AUF {self.guard_height} CM")
        tello.move_up(self.climb)
        start = time.monotonic()
        clear_since: Optional[float] = None
        while True:
            now = time.monotonic()
            height = tello.get_distance_tof()
            valid = self.VALID_CM[0] <= height <= self.VALID_CM[1]
            if valid and height >= self.guard_height - self.DROP_CM:
                clear_since = clear_since or now
            else:
                clear_since = None
            if clear_since is not None and now - clear_since >= self.CLEAR_S:
                logger.info(f"Unter mir frei ({height} cm) – sinke auf {self.flight_height} cm und fliege weiter.")
                break
            if now - start > self.TIMEOUT_S:
                logger.warning(f"Nach {self.TIMEOUT_S:.0f} s noch nicht frei (Sensor {height} cm) – sinke trotzdem zurück.")
                break
            # Auf der Stelle schweben; regelmäßige Befehle, sonst landet die Tello nach 15 s von selbst.
            tello.send_rc_control(0, 0, 0, 0)
            if tick(f"WARTE OBEN ({self.guard_height} CM) – UNTER MIR {height} CM") == "q":
                return False
            time.sleep(0.05)
        tick(f"FREI – SINKE AUF {self.flight_height} CM")
        tello.move_down(self.climb)
        self.reset()
        return True
