from __future__ import annotations

import time
from typing import Optional

import numpy as np

from src.line_follower.classes.gains import Gains
from src.line_follower.classes.line_measurement import LineMeasurement
from src.line_follower.utils import RC_LIMIT, _clamp


class LineController:
    """PID-Regler für den Seitenversatz, PD-Regler für die Drehung (Gier).

    Der D-Anteil dämpft: Bewegt sich die Linie schnell zur Bildmitte, wird schon
    vor dem Erreichen gegengebremst. Der I-Anteil gleicht eine gleichbleibende
    Seitendrift im Vorwärtsflug aus (siehe parse_args, --k-lateral-i).
    """

    INTEGRAL_LIMIT = 30  # max. Beitrag des I-Anteils in RC-Einheiten
    # Größere Sprünge von Bild zu Bild sind keine Bewegung, sondern ein Wechsel des
    # Ziels (anderer Ast, anderes Linienstück). Ihre "Ableitung" würde den D-Anteil
    # bis an den Anschlag treiben (Flug 20.10. 11:43: Gier -70 -> +77 in einem Bild).
    ANGLE_JUMP = 45.0
    OFFSET_JUMP = 0.5
    # Liegt die Linie weiter als FAR_OFFSET außen, reicht der lineare P-Anteil nicht (Flüge 16.11.:
    # Versatz -0,7 bis -0,97, Seitenbefehl nur -17 bis -21; zweimal an derselben Rechtskurve
    # verloren). Dann zusätzlich seitwärts und langsamer vorwärts (bis auf FAR_MIN_FORWARD).
    FAR_OFFSET = 0.35
    FAR_SPAN = 0.5  # ab FAR_OFFSET + FAR_SPAN volle Bremsung
    FAR_MIN_FORWARD = 0.4

    def __init__(self, gains: Gains):
        self.g = gains
        self.reset()

    def reset(self) -> None:
        self._prev_angle = 0.0
        self._prev_offset = 0.0
        self._prev_time: Optional[float] = None
        self._integral = 0.0

    def update(self, line: LineMeasurement) -> tuple[int, int, int]:
        now = time.monotonic()
        d_angle = d_offset = 0.0
        if self._prev_time is not None:
            dt = min(max(now - self._prev_time, 1e-3), 0.2)
            if abs(line.angle - self._prev_angle) < self.ANGLE_JUMP:
                d_angle = (line.angle - self._prev_angle) / dt
            if abs(line.offset - self._prev_offset) < self.OFFSET_JUMP:
                d_offset = (line.offset - self._prev_offset) / dt
            # Nur auf (fast) geraden Stücken aufsummieren: An Ecken ist der Versatz
            # mehrdeutig und würde den I-Anteil falsch aufladen.
            if self.g.k_lateral_i > 0 and abs(line.angle) < 30:
                self._integral += self.g.k_lateral_i * line.offset * dt
                self._integral = float(np.clip(self._integral, -self.INTEGRAL_LIMIT, self.INTEGRAL_LIMIT))
        self._prev_angle, self._prev_offset, self._prev_time = line.angle, line.offset, now

        yaw = self.g.k_yaw * line.angle + self.g.k_yaw_d * d_angle
        # Je stärker die Linie abknickt, desto langsamer vorwärts – an einer 90°-Ecke
        # bleibt die Drohne stehen und dreht, statt über die Ecke hinauszufliegen.
        forward = self.g.forward_speed * max(0.0, np.cos(np.radians(min(abs(line.angle) * 1.5, 90))))
        far = self.far_fraction(line.offset)
        forward *= 1.0 - far * (1.0 - self.FAR_MIN_FORWARD)
        lateral = (self.g.k_lateral * line.offset + self.g.k_lateral_d * d_offset + self._integral
                   + self.curve_feedforward(line.angle, forward)
                   + self.g.k_lateral_far * np.sign(line.offset) * max(0.0, abs(line.offset) - self.FAR_OFFSET))
        return self.out(lateral, self.g.min_rc_lateral), _clamp(forward), self.out(yaw)

    def far_fraction(self, offset: float) -> float:
        """0 bei Versatz bis FAR_OFFSET, 1 ab FAR_OFFSET + FAR_SPAN (Linie am Bildrand)."""
        if self.g.k_lateral_far <= 0:
            return 0.0
        return float(np.clip((abs(offset) - self.FAR_OFFSET) / self.FAR_SPAN, 0.0, 1.0))

    def curve_feedforward(self, angle: float, forward: float) -> float:
        """Seitwärts in die Kurve hineinsteuern, bevor die Drohne nach außen getragen wird.

        Dreht die Drohne im Vorwärtsflug, trägt der Schwung sie nach außen (Fliehkraft ~ Tempo ×
        Drehrate). Der P-Anteil merkt das erst am Versatz und wirkt wegen der ~0,9 s Totzeit zu
        spät: In den Flügen vom 9.11. (11:34, 11:41, 40 cm Höhe) lag die Linie bei 10–45°
        Kurvenwinkel im Mittel bis 0,33 auf der Innenseite, der Seitenbefehl blieb trotzdem bei
        -1 bis +3 – die Tello setzt seitwärts erst ab ~10 um. Flug 11:41 verlor so die Linie
        am Außenrand einer Rechtskurve.

        Die Vorsteuerung nimmt den Kurvenwinkel (wie stark die Linie vorne abknickt, also auch,
        wie schnell die Drohne gleich dreht) mal dem Anteil der Vorwärtsfahrt. Positiver Winkel =
        Rechtskurve -> positiv = nach rechts. Mit k_curve 20: ~5 bei 15°, ~7 bei 30°, ~5 bei 45° (Standard 12: ~3 / ~4 / ~3);
        an Ecken ab 60° steht die Drohne (forward 0) und dreht auf der Stelle -> keine Vorsteuerung.
        Hängt nicht vom Bild ab, deshalb anders als die Seitenregler nicht mit der Höhe skaliert.
        """
        if self.g.k_curve <= 0 or self.g.forward_speed <= 0:
            return 0.0
        bend = np.sin(np.radians(np.clip(angle, -90.0, 90.0)))
        return float(self.g.k_curve * bend * forward / self.g.forward_speed)

    def out(self, value: float, min_rc: Optional[int] = None) -> int:
        """Totzonen-Ausgleich: RC-Werte unter ~min_rc setzt die Tello nicht um (im Flug-Log
        nachgewiesen: 10 s lang Gier -6 ergaben kaum Drehung). Deshalb wird der Stellbereich
        auf [min_rc, 100] abgebildet; Werte unter 1 bleiben 0, damit sie auf der Linie ruhig bleibt.
        Ohne min_rc gilt Gains.min_rc."""
        min_rc = self.g.min_rc if min_rc is None else min_rc
        if abs(value) < 1 or min_rc <= 0:
            return _clamp(value)
        scaled = min_rc + abs(value) * (RC_LIMIT - min_rc) / RC_LIMIT
        return _clamp(np.sign(value) * scaled)
