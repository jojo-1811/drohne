from __future__ import annotations

import argparse
import logging
import math
import time
from dataclasses import dataclass
from typing import Callable, Optional

import cv2
import numpy as np
from djitellopy import Tello

from src.line_follower.classes.flight_error import FlightError
from src.line_follower.classes.video_reader import VideoReader
from src.line_follower.methods.find_wide_objects import find_wide_objects
from src.line_follower.methods.go_to_height import go_to_height
from src.line_follower.methods.preprocess import preprocess
from src.line_follower.methods.remove_blobs import remove_blobs
from src.line_follower.methods.segment_line import segment_line
from src.line_follower.utils import DOWNWARD_FRAME_SHAPE, REFERENCE_HEIGHT_CM, _stripe_width, is_corrupt, log

# Ein Bild für Anzeige/Aufzeichnung: (Graubild, Ansicht mit Markierungen, Status, RC-Befehl lr/fb/yaw)
Publish = Callable[[np.ndarray, np.ndarray, str, tuple[int, int, int]], None]

MIN_ELONGATION = 3.5  # wie LineTracker: Band ist ein langer Streifen, kein Klumpen
LOOK_S = 1.2  # so lange an jedem Suchpunkt nach der Linie schauen
CONFIRM_FRAMES = 3  # so viele Bilder in Folge mit Linie, bevor sie als gefunden gilt
CENTER_TOLERANCE = 0.1  # Linienpunkt höchstens so weit (Anteil halbe Bildbreite/-höhe) neben der Mitte
CENTER_FRAMES = 4  # ... und das so viele Bilder in Folge
CENTER_TIMEOUT_S = 10.0
CENTER_LOST_S = 1.5  # Linie beim Zentrieren so lange weg -> Suche gescheitert
K_CENTER = 30  # RC-Wert pro Anteil Versatz
MAX_RC = 20
MIN_RC = 8  # kleinere RC-Werte setzt die Tello kaum um
MIN_TURN_DEG = 8  # kleinere Abweichungen gleicht das Linienfolgen selbst aus


@dataclass
class FoundLine:
    """Nächstgelegene Linie im Bild."""

    contour: np.ndarray
    point: tuple[int, int]  # Linienpunkt, der der Bildmitte (= Drohne) am nächsten liegt
    angle: float  # Linienrichtung dort in Grad, -90..+90, 0 = geradeaus (Bild oben = vorne)
    ex: float  # Versatz des Punkts zur Mitte: -1 (ganz links) .. +1 (ganz rechts)
    ey: float  # -1 (ganz oben = vor der Drohne) .. +1 (ganz unten = hinter ihr)


def nearest_line(mask: np.ndarray, min_area_px: float) -> Optional[FoundLine]:
    """Unter allen streifenförmigen Flächen die, deren Rand der Bildmitte am nächsten liegt."""
    h, w = mask.shape
    center = (w / 2, h / 2)
    best = None
    for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]:
        area = cv2.contourArea(contour)
        if area < min_area_px:
            continue
        filled = np.zeros_like(mask)
        cv2.drawContours(filled, [contour], -1, 255, cv2.FILLED)
        width = _stripe_width(filled)
        if width <= 0 or area / (width * width) < MIN_ELONGATION:
            continue
        distance = max(0.0, -cv2.pointPolygonTest(contour, center, True))  # innen: 0
        if best is None or distance < best[0]:
            best = (distance, contour)
    if best is None:
        return None
    contour = best[1]
    points = contour.reshape(-1, 2).astype(np.float32)
    nearest = points[np.argmin(np.hypot(points[:, 0] - center[0], points[:, 1] - center[1]))]
    # Richtung aus den Randpunkten in der Umgebung des nächsten Punkts (beide Kanten des Bands)
    radius = max(15.0, 0.1 * w)
    near = points[np.hypot(points[:, 0] - nearest[0], points[:, 1] - nearest[1]) <= radius]
    vx, vy = cv2.fitLine(near if len(near) >= 5 else points, cv2.DIST_L2, 0, 0.01, 0.01).flatten()[:2]
    angle = math.degrees(math.atan2(vx, -vy))
    # Von den zwei Richtungen der Linie die, die näher an der Blickrichtung der Drohne liegt
    if angle > 90:
        angle -= 180
    elif angle <= -90:
        angle += 180
    px, py = int(round(nearest[0])), int(round(nearest[1]))
    return FoundLine(contour, (px, py), angle, (px - center[0]) / center[0], (py - center[1]) / center[1])


def _draw(gray: np.ndarray, mask: np.ndarray, found: Optional[FoundLine], status: str) -> np.ndarray:
    view = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    h, w = gray.shape
    cv2.drawMarker(view, (w // 2, h // 2), (255, 255, 0), cv2.MARKER_CROSS, 20, 1)
    if found is not None:
        cv2.drawContours(view, [found.contour], -1, (0, 255, 0), 2)
        cv2.circle(view, found.point, 5, (0, 0, 255), -1)
        a = math.radians(found.angle)
        tip = (int(found.point[0] + 40 * math.sin(a)), int(found.point[1] - 40 * math.cos(a)))
        cv2.arrowedLine(view, found.point, tip, (0, 0, 255), 2)
        status += f" | dx {found.ex:+.2f} dy {found.ey:+.2f} Richtung {found.angle:+.0f}"
    cv2.putText(view, status, (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    view[h - h // 3:, w - w // 3:] = cv2.resize(cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), (w // 3, h // 3))
    return view


def _rc(error: float) -> int:
    value = max(-MAX_RC, min(MAX_RC, K_CENTER * error))
    if abs(value) < 1:
        return 0
    return int(round(math.copysign(max(MIN_RC, abs(value)), value)))


class _Camera:
    """Liefert jedes neue Kamerabild einmal, schon ausgewertet."""

    def __init__(self, reader: VideoReader, args: argparse.Namespace, height_cm: int) -> None:
        self.reader, self.args = reader, args
        self._last = None
        # Aus größerer Höhe ist das Band schmaler und kürzer im Bild -> Mindestfläche mitskalieren
        # (Flug 16.11. 10:28 auf 100 cm: Linienstücke mit 0,8 % Fläche, Grenze war 1 %).
        h, w = DOWNWARD_FRAME_SHAPE
        self.min_area_px = args.min_area * h * w * min(1.0, REFERENCE_HEIGHT_CM / max(1, height_cm))

    def next(self, timeout_s: float = 0.5) -> Optional[tuple[np.ndarray, np.ndarray, Optional[FoundLine]]]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            frame = self.reader.frame
            if frame is not None and frame is not self._last:
                self._last = frame
                args = self.args
                gray = preprocess(frame, args.rotate, args.flip)
                if frame.shape[:2] != DOWNWARD_FRAME_SHAPE or is_corrupt(gray, args.corrupt_threshold):
                    return gray, np.zeros_like(gray), None
                mask = segment_line(gray, line_is_dark=not args.light_line, threshold=args.threshold,
                                    contrast=args.contrast)
                objects = find_wide_objects(gray, not args.light_line, args.max_line_width, args.contrast)
                mask = remove_blobs(mask, objects, args.max_line_width)
                return gray, mask, nearest_line(mask, self.min_area_px)
            time.sleep(0.01)
        return None


# Suchpunkte als Wege (Richtung, Faktor × Radius) vom vorigen Punkt aus: Quadrat um den Startpunkt.
_PATTERN = (("forward", 1), ("right", 1), ("back", 1), ("back", 1), ("left", 1), ("left", 1),
            ("forward", 1), ("forward", 1))
_GERMAN = {"forward": "vor", "back": "zurück", "left": "links", "right": "rechts"}


def search_line(tello: Tello, reader: VideoReader, args: argparse.Namespace, publish: Publish,
                logger: logging.Logger = log) -> None:
    """Nach dem Abheben neben der Linie: höher steigen, die nächste Linie suchen, sich über
    sie stellen, in ihre Richtung drehen und auf Flughöhe sinken.

    Aus args.search_height sieht die Kamera etwa viermal so viel Boden wie aus 50 cm. Ist dort
    keine Linie, werden die Ecken und Seitenmitten eines Quadrats mit args.search_radius um den
    Startpunkt abgeflogen. Von zwei möglichen Richtungen der Linie wird die genommen, die näher
    an der Blickrichtung der Drohne liegt. Ohne Linie: FlightError (fly() landet dann).
    """
    go_to_height(tello, args.search_height, logger=logger)
    camera = _Camera(reader, args, args.search_height)
    logger.info(f"Suche die Linie aus {args.search_height} cm Höhe.")

    found = None
    for step, move in enumerate((None,) + _PATTERN):
        if move is not None:
            direction, factor = move
            distance = max(20, int(args.search_radius * factor))  # Tello fliegt erst ab 20 cm
            publish_status = f"SUCHE – {_GERMAN[direction]} {distance} cm ({step}/{len(_PATTERN)})"
            logger.info(f"Keine Linie in Sicht – {publish_status.split('– ')[1]}.")
            getattr(tello, f"move_{direction}")(distance)
        found = _look(camera, publish, f"SUCHE ({step}/{len(_PATTERN)})")
        if found is not None:
            break
    if found is None:
        raise FlightError("Keine Linie in der Umgebung gefunden – lande.")
    logger.info(f"Linie gefunden ({found.ex:+.2f} / {found.ey:+.2f} neben der Mitte) – stelle mich darüber.")

    found = _center(tello, camera, publish)
    if found is None:
        raise FlightError("Linie beim Ausrichten über ihr verloren – lande.")
    if abs(found.angle) >= MIN_TURN_DEG:
        logger.info(f"Drehe {abs(found.angle):.0f}° nach {'rechts' if found.angle > 0 else 'links'} in Linienrichtung.")
        if found.angle > 0:
            tello.rotate_clockwise(int(round(found.angle)))
        else:
            tello.rotate_counter_clockwise(int(round(-found.angle)))
    go_to_height(tello, args.height, logger=logger)
    logger.info("Über der Linie – folge ihr.")


def _look(camera: _Camera, publish: Publish, status: str) -> Optional[FoundLine]:
    """An der aktuellen Stelle schauen, ob eine Linie (mehrere Bilder in Folge) zu sehen ist."""
    deadline = time.monotonic() + LOOK_S
    streak = 0
    while time.monotonic() < deadline:
        result = camera.next()
        if result is None:
            continue
        gray, mask, found = result
        publish(gray, _draw(gray, mask, found, status), status, (0, 0, 0))
        streak = streak + 1 if found is not None else 0
        if streak >= CONFIRM_FRAMES:
            return found
    return None


def _center(tello: Tello, camera: _Camera, publish: Publish) -> Optional[FoundLine]:
    """Mit RC-Befehlen über den nächstgelegenen Linienpunkt schieben. Gibt die letzte Messung zurück."""
    start = last_seen = time.monotonic()
    good = 0
    found = None
    try:
        while time.monotonic() - start < CENTER_TIMEOUT_S:
            result = camera.next()
            now = time.monotonic()
            if result is None or result[2] is None:
                tello.send_rc_control(0, 0, 0, 0)
                if now - last_seen > CENTER_LOST_S:
                    return None
                if result is not None:
                    publish(result[0], _draw(result[0], result[1], None, "ÜBER LINIE STELLEN"), "ÜBER LINIE STELLEN",
                            (0, 0, 0))
                continue
            gray, mask, found = result
            last_seen = now
            if abs(found.ex) <= CENTER_TOLERANCE and abs(found.ey) <= CENTER_TOLERANCE:
                good += 1
                lr = fb = 0
            else:
                good = 0
                lr, fb = _rc(found.ex), _rc(-found.ey)  # Punkt oben im Bild = vor der Drohne
            tello.send_rc_control(lr, fb, 0, 0)
            status = f"ÜBER LINIE STELLEN lr {lr:+d} fb {fb:+d}"
            publish(gray, _draw(gray, mask, found, status), status, (lr, fb, 0))
            if good >= CENTER_FRAMES:
                return found
        return found  # nicht ganz mittig, aber Linie in Sicht – das Linienfolgen gleicht den Rest aus
    finally:
        tello.send_rc_control(0, 0, 0, 0)
