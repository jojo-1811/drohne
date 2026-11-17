from __future__ import annotations

import logging
import time

import av
from djitellopy import Tello, TelloException

from src.line_follower.classes.video_reader import VideoReader
from src.line_follower.utils import DOWNWARD_FRAME_SHAPE, log


def start_video(tello: Tello, attempts: int = 3, timeout_s: float = 5.0,
                logger: logging.Logger = log, bitrate: int = Tello.BITRATE_AUTO) -> VideoReader:
    """Stream der unteren Kamera starten und warten, bis Bilder im richtigen Format kommen.

    Beim Umschalten auf die untere Kamera liefert die Drohne manchmal dauerhaft
    320×720 statt 320×240 Pixel (nur das untere Drittel ist das Kamerabild, der
    Rest Datenmüll – beobachtet am 19.10. und 20.10.). Dann wird der Stream
    neu gestartet, statt mit dem kaputten Format zu fliegen.

    bitrate: Videobitrate in Mbit/s (1–5, Tello.BITRATE_*); 0 = automatisch. Gemessen am
    2.11. (untere Kamera): automatisch ~1,7 Mbit/s, mit 1 ~1,2 Mbit/s bei gleicher Bildrate.
    """
    for attempt in range(1, attempts + 1):
        tello.streamon()
        tello.set_video_direction(Tello.CAMERA_DOWNWARD)
        if bitrate != Tello.BITRATE_AUTO:
            tello.set_video_bitrate(bitrate)
        try:
            reader = VideoReader(tello.get_udp_video_address(), open_timeout=timeout_s)
        except av.error.FFmpegError as error:
            logger.warning(f"Video startet nicht ({error}) – Versuch {attempt}/{attempts}.")
            tello.streamoff()
            continue
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            frame = reader.frame
            if frame is not None and frame.shape[:2] == DOWNWARD_FRAME_SHAPE:
                return reader
            time.sleep(0.05)
        shape = None if reader.frame is None else "x".join(map(str, reader.frame.shape[1::-1]))
        logger.warning(f"Video startet nicht richtig (Bildformat {shape or 'kein Bild'}) – Versuch {attempt}/{attempts}.")
        reader.stop()
        tello.streamoff()
    raise TelloException("Kein Videobild im richtigen Format von der unteren Kamera.")
