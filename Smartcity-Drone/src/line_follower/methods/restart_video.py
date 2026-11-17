from __future__ import annotations

import logging
from typing import Optional

from djitellopy import Tello, TelloException

from src.line_follower.classes.video_reader import VideoReader
from src.line_follower.methods.start_video import start_video
from src.line_follower.utils import log


def restart_video(tello: Tello, reader: Optional[VideoReader],
                  logger: logging.Logger = log, bitrate: int = Tello.BITRATE_AUTO) -> Optional[VideoReader]:
    """Videostream aus- und wieder einschalten; liefert den neuen Reader oder None."""
    if reader is not None:
        reader.stop()
    try:
        tello.streamoff()
        # Nur ein kurzer Versuch: Die Drohne bekommt währenddessen keine Befehle und landet
        # nach 15 s von selbst. Weitere Versuche steuert VideoGuard aus der Hauptschleife.
        return start_video(tello, attempts=1, timeout_s=3.0, logger=logger, bitrate=bitrate)
    except TelloException as error:
        logger.warning(f"Videoneustart fehlgeschlagen: {error}")
        return None
