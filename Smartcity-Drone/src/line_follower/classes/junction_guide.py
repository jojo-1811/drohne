from __future__ import annotations

from typing import Optional

import numpy as np

from src.line_follower.classes.line_measurement import LineMeasurement


class JunctionGuide:
    """Hält an Abzweigungen an und fragt, welcher Ast genommen werden soll.

    Eine Abzweigung gilt erst als erkannt, wenn sie in CONFIRM_FRAMES Bildern
    hintereinander zu sehen ist (einzelne Fehlbilder an Ecken lösen nichts aus).

    Nach der Wahl wird erst wieder gefragt, wenn die Kreuzung hinter der Drohne
    liegt: CLEAR_AFTER Sekunden lang keine Abzweigung im Bild UND seit der Wahl
    ein Stück vorwärts geflogen (Summe der Vorwärtsbefehle × Zeit ≥ CLEAR_TRAVEL,
    z. B. 0,4 s mit fb 25). Die Zeit allein reicht nicht – beim Drehen über der
    Kreuzung liegt diese unter der Drohne (dort werden keine Äste gezählt) und
    taucht nach der Drehung wieder vorn auf; sie würde ein zweites Mal abgefragt
    (Flug 20.10. 11:43). Beim Drehen auf der Stelle kommt kein Vorwärtsweg zusammen.
    Der Vorwärtsweg allein reicht auch nicht: Driftet die Drohne seitlich, bleibt
    dieselbe Kreuzung länger im Bild.
    """

    CONFIRM_FRAMES = 3
    APPROACH_SPEED = 0.4  # Anteil der Vorwärtsgeschwindigkeit, sobald eine Abzweigung in Sicht ist
    HOLD_ROW = 1 / 6  # Kreuzung beim Warten so weit (Anteil der Bildhöhe) vor der Drohne halten
    HOLD_GAIN = 60.0  # fb pro Bildhöhe Abweichung
    HOLD_MAX = 20
    HOLD_DEADBAND = 0.06
    HOLD_MEMORY = 1.0
    # RC-Einheiten × Sekunden. Im Flug 11:43 kamen beim Drehen über einer Kreuzung 0,1
    # zusammen, bis zur nächsten, ~1 s entfernten Kreuzung 17.
    CLEAR_TRAVEL = 10.0
    CLEAR_AFTER = 1.5
    KEYS = {"a": "links", "w": "geradeaus", "d": "rechts"}
    SIDES = {"a": -1, "w": 0, "d": 1}

    def __init__(self) -> None:
        self.options: dict[str, float] = {}  # Taste -> Richtung des Astes (Grad)
        self.asked_at: Optional[float] = None
        self._sided = False  # Tasten nach Seite (links/geradeaus/rechts) vergeben, siehe label
        self._seen = 0
        self._travel = 0.0
        self._last_update: Optional[float] = None
        self._last_junction = -np.inf
        self._hold, self._hold_time = 0, -np.inf
        self._handled = False

    @property
    def waiting(self) -> bool:
        return bool(self.options)

    @property
    def passing(self) -> bool:
        """Ast gewählt, die Abzweigung ist aber noch im Bild."""
        return self._handled

    def update(self, line: Optional[LineMeasurement], now: float, forward: int) -> None:
        """forward: zuletzt gesendeter Vorwärtsbefehl (fb) – Maß für den zurückgelegten Weg."""
        junction = line is not None and len(line.branches) >= 2
        self._seen = self._seen + 1 if junction else 0
        if junction:
            self._last_junction = now
        if self._handled and self._last_update is not None:
            self._travel += max(forward, 0) * min(now - self._last_update, 0.2)
            if self._travel >= self.CLEAR_TRAVEL and now - self._last_junction > self.CLEAR_AFTER:
                self._handled = False
        self._last_update = now
        if self.waiting and line is not None and line.branches:
            self._track_options(line.branches)
        elif not self.waiting and not self._handled and self._seen >= self.CONFIRM_FRAMES:
            self.options = self.label(line.branches)
            self._sided = len({side for _, side in line.branches}) >= 2
            self.asked_at = now

    def _track_options(self, branches: tuple[tuple[float, int], ...]) -> None:
        """Richtungen der angebotenen Äste nachführen – die Drohne bewegt sich beim Schweben.

        Die Beschriftung bleibt, wie sie angezeigt wurde. Jeder Ast im Bild gehört zu
        höchstens einer Taste, und zwar nur zu einer derselben Seite. Ist der Ast einer
        Taste gerade nicht zu sehen (z. B. liegt er beim Zurücksetzen auf Höhe der Drohne
        und zählt nicht als Ast vorn), behält sie ihre letzte Richtung. Vorher bekam jede
        Taste die nächstgelegene Richtung im Bild: War nur noch geradeaus zu sehen, zeigten
        auch "links" und "rechts" dauerhaft dorthin (Flug 26.10. 11:25: dreimal links
        gewählt, dreimal geradeaus geflogen).
        """
        pairs = sorted(
            (abs(bearing - old), key, bearing)
            for key, old in self.options.items()
            for bearing, side in branches
            if not self._sided or side == self.SIDES[key])
        assigned: set[float] = set()
        for _, key, bearing in pairs:
            if key in assigned or bearing in assigned:
                continue
            self.options[key] = bearing
            assigned.update((key, bearing))

    @property
    def approaching(self) -> bool:
        """Eine Abzweigung ist in Sicht, aber (noch) nicht bestätigt bzw. gefragt."""
        return self._seen > 0 and not self.waiting and not self._handled

    def hold_forward(self, line: Optional[LineMeasurement], h: int, now: float) -> int:
        """Vorwärtsbefehl, der die Kreuzung beim Warten knapp vor der Drohne hält.

        Die Drohne gleitet nach dem Anhalten mit Schwung weiter (Flug 11:43: rund
        eine Bildhöhe, die Kreuzung war danach aus dem Bild). Deshalb wird auf die
        Lage der Kreuzung im Bild geregelt: Liegt sie weiter vorn als HOLD_ROW,
        langsam vor, ist sie zu nah oder schon hinter der Drohne, zurück. Ist sie
        kurz nicht zu finden (z. B. gerade am unteren Bildrand), gilt der letzte
        Befehl noch HOLD_MEMORY Sekunden weiter.
        """
        if line is None or line.junction is None:
            return self._hold if now - self._hold_time < self.HOLD_MEMORY else 0
        error = (line.junction[1] - (line.anchor[1] - self.HOLD_ROW * h)) / h  # + = Kreuzung zu nah/dahinter
        command = 0 if abs(error) < self.HOLD_DEADBAND else \
            int(np.clip(-self.HOLD_GAIN * error, -self.HOLD_MAX, self.HOLD_MAX))
        self._hold, self._hold_time = command, now
        return command

    def choose(self, key: str) -> Optional[float]:
        """Richtung des gewählten Astes, oder None bei einer Taste ohne Ast."""
        if key not in self.options:
            return None
        bearing = self.options[key]
        self.options = {}
        self.asked_at = None
        self._handled = True
        self._travel = 0.0
        return bearing

    def prompt(self) -> str:
        return "  ".join(f"{key}: {self.KEYS[key]}" for key in self.options) + "  l: landen"

    @staticmethod
    def label(branches: tuple[tuple[float, int], ...]) -> dict[str, float]:
        """Äste den Tasten zuordnen: links -> a, geradeaus -> w, rechts -> d.

        Liegen zwei Äste auf derselben Seite, bekommt der flachere "geradeaus".
        """
        options: dict[str, float] = {}
        for side, key in ((0, "w"), (-1, "a"), (1, "d")):
            candidates = [bearing for bearing, branch_side in branches if branch_side == side]
            if candidates:
                options[key] = min(candidates, key=abs) if side == 0 else max(candidates, key=abs)
        if len(options) < 2:
            ordered = sorted((bearing for bearing, _ in branches), key=abs)
            options = {"w": ordered[0], ("a" if ordered[-1] < 0 else "d"): ordered[-1]}
        return options
