from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# Ereignisse (Abzweigung, Auto, Landung, Videostörung …) – auf der Konsole und bei --record
# zusätzlich in events.log der Aufzeichnung, mit derselben Zeitachse wie log.csv.
log = logging.getLogger("line_follower")

# Eigene Stufe für Erfolgsmeldungen (abgehoben, gelandet, über dem Pad) – im Webinterface grün.
SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")

RC_LIMIT = 100

# Flughöhe, bei der der Seitenregler vermessen wurde (Flüge 26.10./2.11.: 53–57 cm nach
# Abheben und 30 cm Absinken; Sprungtest 26.10.). Andere Höhen skalieren die Werte, siehe fly().
REFERENCE_HEIGHT_CM = 55

RECORDINGS_DIR = Path(__file__).resolve().parents[2] / "recordings"  # recordings/ im Projektordner

# Kleinste Fläche (Anteil des Bildes), ab der ein breites Objekt als Auto zählt. Die
# Roboter der Smart City bedeckten in den Flügen vom 26.10. 3–11 % des Bildes.
CAR_MIN_AREA = 0.02

CAR_MIN_HIT = 0.005  # so viel Autofläche (Anteil des Bildes) muss im Fahrkorridor liegen

KEEP_MAX_JUMP = 60  # Grad: so weit darf das Ziel nach einer Wahl an der Abzweigung pro Bild springen

MAX_END_BEARING = 120  # Grad: Linienenden weiter hinten zählen nicht als Ziel

DOWNWARD_FRAME_SHAPE = (240, 320)  # Höhe, Breite eines Bildes der unteren Kamera

FRAME_TIMEOUT = 0.5  # Sekunden ohne neues Kamerabild -> Videostream gilt als ausgefallen

PAD_TOLERANCE_CM = 5  # so weit darf die Drohne vor dem Landen neben der Padmitte stehen



def _clamp(value: float) -> int:
    return int(np.clip(value, -RC_LIMIT, RC_LIMIT))


def _stripe_width(filled: np.ndarray) -> float:
    """Typische Breite eines Streifens: Median des Abstands zum Rand entlang seiner Mittellinie.

    Das Maximum wäre an Kreuzungen, T-Stücken und Ecken viel zu groß – dort ist
    die Fläche breiter als das Band selbst, die Linie würde als "zu breit" verworfen.
    """
    distance = cv2.distanceTransform(filled, cv2.DIST_L2, 5)
    ridge = (distance > 0) & (distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)))
    return 2.0 * float(np.median(distance[ridge])) if ridge.any() else 0.0


def _int_point(p: np.ndarray) -> tuple[int, int]:
    return int(round(p[0])), int(round(p[1]))


def _read_key(delay_ms: int) -> Optional[str]:
    """Taste aus dem Videofenster (nur Kleinbuchstaben-Zeichen), None wenn keine gedrückt wurde."""
    code = cv2.waitKey(delay_ms)
    return chr(code & 0xFF).lower() if code != -1 else None


def is_corrupt(gray: np.ndarray, threshold: float) -> bool:
    """Ist das Bild durch Übertragungsfehler zerstört (Klötzchen-Artefakte)?

    Fehlen dem H.264-Decoder Daten, setzt er Bildblöcke falsch zusammen; das
    Bild besteht dann großflächig aus harten Kanten. Gemessen an der mittleren
    Kantenstärke (Laplace): Ungestörte Bilder lagen in allen Testflügen bei
    15–30, zerstörte im Median bei 75. Scharfe Nahaufnahmen eines Roboters direkt
    unter der Kamera erreichen aber auch knapp 40 (Flug 26.10. 12:14: zwei unnötige
    Kamera-Neustarts) – deshalb liegt die Schwelle bei 50. Schwach gestörte Bilder
    am Rand einer Störung (40–50) werden so nicht erkannt; für den Neustart zählt
    ohnehin der Anteil gestörter Bilder in einer Sekunde (siehe VideoGuard).
    """
    return float(np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3)).mean()) > threshold
