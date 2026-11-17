from __future__ import annotations

import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from djitellopy import Tello

from src.line_follower.classes.line_measurement import LineMeasurement
from src.line_follower.utils import log


class FlightRecorder:
    """Zeichnet einen Flug auf: Overlay-Video, Rohbild-Video (zum exakten Nachstellen
    mit geänderter Erkennung) und CSV mit Mess- und Steuerwerten pro Bild."""

    FIELDS = ["t", "frame_shape", "corrupt", "decoder_corrupt", "line", "offset", "angle", "lr", "fb", "yaw", "status", "height_cm", "mission_pad", "pad_x", "pad_y", "roll", "pitch", "yaw_deg", "vgx", "vgy",
              "battery", "temp_c"]

    def __init__(self, base_dir: Path, fps: float = 20.0, logger: logging.Logger = log, name: str = ""):
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = base_dir / (f"{stamp}_{name}" if name else stamp)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fps = fps
        self._video: Optional[cv2.VideoWriter] = None
        self._raw: Optional[cv2.VideoWriter] = None
        self._csv_file = open(self.dir / "log.csv", "w", newline="")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=self.FIELDS)
        self._csv.writeheader()
        self._t0 = time.monotonic()
        self._events = logging.FileHandler(self.dir / "events.log", encoding="utf-8")
        self._events.setFormatter(logging.Formatter("%(flight_time)8.3f %(message)s"))
        self._events.addFilter(self._add_flight_time)
        self._logger = logger
        logger.addHandler(self._events)

    def _add_flight_time(self, record: logging.LogRecord) -> bool:
        record.flight_time = time.monotonic() - self._t0  # gleiche Zeitachse wie Spalte t in log.csv
        return True

    def write(self, frame_shape: Optional[tuple[int, ...]], gray: np.ndarray, view: np.ndarray, line: Optional[LineMeasurement],
              command: tuple[int, int, int], status: str, tello: Tello, corrupt: bool, decoder_corrupt: bool) -> None:
        if self._video is None:
            self._size = (view.shape[1], view.shape[0])
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._video = cv2.VideoWriter(str(self.dir / "video.mp4"), fourcc, self._fps, self._size)
            self._raw = cv2.VideoWriter(str(self.dir / "raw.mp4"), fourcc, self._fps, self._size)
        # Ändert sich das Bildformat unterwegs, auf die Videogröße skalieren – Original steht in frame_shape.
        fit = lambda img: img if (img.shape[1], img.shape[0]) == self._size else cv2.resize(img, self._size)
        self._video.write(fit(view))
        self._raw.write(fit(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)))
        lr, fb, yaw = command
        self._csv.writerow({
            "t": f"{time.monotonic() - self._t0:.3f}",
            "frame_shape": "x".join(map(str, frame_shape[:2])) if frame_shape else "",  # leer: kein Videostream
            "corrupt": int(corrupt),  # eigene Erkennung (is_corrupt)
            "decoder_corrupt": int(decoder_corrupt),  # Flag des H.264-Decoders – zum Vergleich
            "line": int(line is not None),
            "offset": f"{line.offset:.3f}" if line else "",
            "angle": f"{line.angle:.1f}" if line else "",
            "lr": lr, "fb": fb, "yaw": yaw,
            "status": status,
            # Werte aus den Statuspaketen der Drohne – kein zusätzlicher Befehl nötig.
            "height_cm": tello.get_distance_tof(),
            "mission_pad": tello.get_mission_pad_id(),
            # Lage der Drohne relativ zur Padmitte (cm), nur gültig, wenn ein Pad erkannt ist.
            "pad_x": tello.get_mission_pad_distance_x(), "pad_y": tello.get_mission_pad_distance_y(),
            "roll": tello.get_roll(), "pitch": tello.get_pitch(), "yaw_deg": tello.get_yaw(),
            "vgx": tello.get_speed_x(), "vgy": tello.get_speed_y(),
            "battery": tello.get_battery(),
            "temp_c": tello.get_temperature(),
        })

    def close(self) -> None:
        for writer in (self._video, self._raw):
            if writer is not None:
                writer.release()
        self._csv_file.close()
        self._logger.removeHandler(self._events)
        self._events.close()
        print(f"Aufzeichnung gespeichert: {self.dir}")
