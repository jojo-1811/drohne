from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Gains:
    forward_speed: int
    k_lateral: float
    k_lateral_d: float
    k_lateral_i: float
    k_yaw: float
    k_yaw_d: float
    min_rc: int = 0  # Totzonen-Ausgleich für Drehung und Vor/Zurück (siehe LineController.out)
    min_rc_lateral: int = 0  # dasselbe für seitwärts – dort nicht nötig, siehe parse_args
    k_curve: float = 0.0  # Kurven-Vorsteuerung seitwärts (siehe LineController.update), 0 = aus
    k_lateral_far: float = 0.0  # zusätzlich seitwärts, wenn die Linie weit außen liegt (LineController.far_term)
