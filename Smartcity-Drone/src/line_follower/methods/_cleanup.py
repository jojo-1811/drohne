from __future__ import annotations

import logging
from typing import Callable

from djitellopy import TelloException


def _cleanup(logger: logging.Logger, what: str, action: Callable[..., object], *args: object,
             level: int = logging.WARNING, hint: str = "") -> bool:
    """Einen Aufräumschritt ausführen; ein Fehler wird gemeldet, hält die übrigen aber nicht auf."""
    try:
        action(*args)
        return True
    except TelloException as error:
        logger.log(level, f"{what} fehlgeschlagen ({error})" + (f" – {hint}." if hint else "."))
        return False
