from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FlightState:
    """Was eine Oberfläche über den laufenden Flug anzeigt."""

    status: str = "VERBINDE"
    prompt: tuple[str, ...] = ()  # an einer Abzweigung angebotene Tasten (a/w/d), sonst leer
    battery: Optional[int] = None
    height_cm: Optional[int] = None
    temp_c: Optional[float] = None
    speed_cm_s: Optional[int] = None  # Geschwindigkeit über Grund (Statuspakete, Auflösung 10 cm/s)
    heading_deg: Optional[int] = None  # Gierwinkel laut Drohne
    offset: Optional[float] = None  # Lage der Linie, siehe LineMeasurement
    angle: Optional[float] = None
    command: tuple[int, int, int] = (0, 0, 0)  # zuletzt gesendet: seitwärts, vor/zurück, drehen
    flying: bool = False
