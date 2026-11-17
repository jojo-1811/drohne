from __future__ import annotations

from typing import Optional


class PadWatcher:
    """Erkennt das Mission Pad am Ende der Strecke.

    Das Pad, von dem die Drohne startet, soll keine Landung auslösen. Deshalb wird
    erst scharf geschaltet, wenn ARM_AFTER Sekunden lang im Flug kein Pad zu sehen
    war; das nächste erkannte Pad ist dann das Ziel – sobald es CONFIRM_FRAMES Bilder
    hintereinander erkannt ist (ein kurz am Bildrand aufblitzendes Pad zählt nicht).
    """

    ARM_AFTER = 1.0
    CONFIRM_FRAMES = 3

    def __init__(self) -> None:
        self.armed = False
        self._no_pad_since: Optional[float] = None
        self._seen = 0

    def update(self, pad_id: int, now: float) -> Optional[int]:
        if pad_id < 1:
            self._seen = 0
            if self._no_pad_since is None:
                self._no_pad_since = now
            if now - self._no_pad_since >= self.ARM_AFTER:
                self.armed = True
            return None
        self._no_pad_since = None
        self._seen += 1
        return pad_id if self.armed and self._seen >= self.CONFIRM_FRAMES else None
