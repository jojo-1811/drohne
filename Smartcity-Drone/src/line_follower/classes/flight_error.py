from __future__ import annotations

class FlightError(Exception):
    """Der Flug kann nicht starten (Akku, Temperatur, Video …) oder wurde abgebrochen.

    logged: Die Meldung steht schon im Log (und in events.log der Aufnahme) – der
    Aufrufer zeigt sie nur noch an, statt sie ein zweites Mal zu protokollieren.
    """

    def __init__(self, message: str, logged: bool = False) -> None:
        super().__init__(message)
        self.logged = logged
