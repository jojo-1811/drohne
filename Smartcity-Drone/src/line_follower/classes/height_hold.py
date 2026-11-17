from __future__ import annotations

from typing import Optional

from src.line_follower.utils import _clamp


class HeightHold:
    """Hält die Flughöhe während des Flugs (Abstandssensor nach unten), nicht nur beim Start.

    Die Tello hält ihre Höhe selbst, verliert aber den Bezug, wenn sich der Boden unter ihr
    plötzlich ändert. Flug 9.11. 12:16: Ein Auto fuhr unter der Drohne durch (Abstand 52 ->
    25 cm), danach hielt sie dauerhaft ~39 statt 50 cm – bis zur Landung zwei Minuten später.
    go_to_height stellt die Höhe nur einmal nach dem Abheben ein.

    Geregelt wird über den Hoch/Runter-Kanal der RC-Befehle, behutsam:
    - erst wenn die Abweichung TOLERANCE_CM länger als DELAY_S anhält (kurze Ausreißer,
      Bodenwellen, Klebeband-Rollen ignorieren) und dann bis auf SETTLE_CM heran (Hysterese,
      damit sie nicht ständig hoch und runter pendelt);
    - nie, solange etwas Fremdes unter oder vor der Drohne sein kann (Auto im Bild, Warten
      vor einem Auto, kein Videobild): Über einem Auto misst der Sensor das Autodach, die
      Drohne würde absinken. Danach PAUSE_S warten, bis der Sensor wieder den Boden sieht;
    - ein Sprung des Messwerts um mehr als JUMP_CM zwischen zwei Bildern gilt ebenfalls als
      Fremdkörper (Auto unbemerkt unter der Drohne, z. B. während eines Video-Aussetzers wie
      im Flug 12:16) und pausiert die Regelung.
    """

    TOLERANCE_CM = 8
    SETTLE_CM = 3
    DELAY_S = 2.0
    PAUSE_S = 2.0
    JUMP_CM = 12
    GAIN = 1.0  # RC-Einheiten pro cm Abweichung
    MIN_RC = 10  # kleinere Werte setzt die Tello kaum um
    MAX_RC = 20  # ~20 cm/s – sanft, damit die Linie im Bild nicht springt
    VALID_CM = (10, 300)  # außerhalb: Sensor misst nichts Brauchbares

    def __init__(self, target_cm: int) -> None:
        self.target = target_cm
        self.correcting = False
        self._off_since: Optional[float] = None
        self._paused_until = 0.0
        self._last: Optional[float] = None

    def pause(self, now: float) -> None:
        """Fremdkörper möglich: nicht regeln, bis PAUSE_S lang wieder freie Sicht war."""
        self._paused_until = now + self.PAUSE_S
        self._off_since = None
        self.correcting = False

    def update(self, height_cm: float, now: float, blocked: bool = False) -> int:
        """Hoch/Runter-Befehl (positiv = steigen) für den aktuellen Messwert.

        blocked: Auto im Bild, Warten vor einem Auto oder kein brauchbares Bild.
        """
        last, self._last = self._last, height_cm
        if not self.VALID_CM[0] <= height_cm <= self.VALID_CM[1]:
            self.pause(now)
            return 0
        if blocked or (last is not None and abs(height_cm - last) > self.JUMP_CM):
            self.pause(now)
            return 0
        if now < self._paused_until:
            return 0

        error = self.target - height_cm
        if self.correcting:
            if abs(error) <= self.SETTLE_CM:
                self.correcting = False
                self._off_since = None
                return 0
        elif abs(error) > self.TOLERANCE_CM:
            if self._off_since is None:
                self._off_since = now
            if now - self._off_since < self.DELAY_S:
                return 0
            self.correcting = True
        else:
            self._off_since = None
            return 0

        command = max(self.MIN_RC, min(self.MAX_RC, abs(error) * self.GAIN))
        return _clamp(command if error > 0 else -command)
