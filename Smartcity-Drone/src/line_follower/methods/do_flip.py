from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from djitellopy import Tello, TelloException

from src.line_follower.methods.go_to_height import MIN_STEP_CM, go_to_height
from src.line_follower.utils import log

# Tasten für einen Flip: im Browser die vier Flip-Knöpfe, im Videofenster "f" (rückwärts).
# Wert = Richtung für den SDK-Befehl "flip".
FLIP_KEYS = {"f": "b", "flip-f": "f", "flip-b": "b", "flip-l": "l", "flip-r": "r",
             # Synchron-Flip (Dashboard): alle fliegenden Drohnen steigen, warten aufeinander, flippen zusammen
             "sync-flip-f": "f", "sync-flip-b": "b", "sync-flip-l": "l", "sync-flip-r": "r"}
# So lange wartet eine Drohne beim Synchron-Flip auf die anderen (Steigen dauert je nach Netz 2–8 s)
SYNC_TIMEOUT_S = 12.0
FLIP_NAMES = {"f": "vorwärts", "b": "rückwärts", "l": "links", "r": "rechts"}
# Die Tello lehnt Flips unter ~50 % Akku selbst ab ("error"). Gemessen wird dabei unter Last:
# Flug 9.11. 13:41 – Flip bei 52 % angefordert, nach dem Steigen abgelehnt (Akku dann ~50 %).
# Deshalb mit Reserve vorher prüfen, damit im Log ein verständlicher Grund steht.
FLIP_MIN_BATTERY = 60
# Ein Flip kostet die Tello gut 20–30 cm Höhe, bevor sie sich fängt. Aus 40–55 cm Flughöhe
# wäre das knapp über dem Boden – deshalb erst auf diese Höhe steigen.
FLIP_HEIGHT_CM = 100
# Ein Flip nach links/rechts versetzt die Drohne seitlich, die Linie liegt danach meist neben dem
# Bild. Dann so lange in die Gegenrichtung schieben, bis die Linie wieder da ist (höchstens
# SIDE_SEARCH_S, danach greift die normale Landung nach --lost-timeout).
SIDE_SEARCH_RC = 20
SIDE_SEARCH_S = 5.0


def sync_wait(barrier: Optional[threading.Barrier], logger: logging.Logger = log) -> bool:
    """Beim Synchron-Flip auf die anderen Drohnen warten. True = alle bereit (oder kein Synchron-Flip)."""
    if barrier is None:
        return True
    try:
        barrier.wait(SYNC_TIMEOUT_S)
        return True
    except threading.BrokenBarrierError:
        logger.warning(f"Synchron-Flip: Nicht alle Drohnen waren nach {SYNC_TIMEOUT_S:.0f} s bereit – kein Flip.")
        return False


def do_flip(tello: Tello, direction: str, height_cm: int, logger: logging.Logger = log,
            flip_height_cm: int = FLIP_HEIGHT_CM, barrier: Optional[threading.Barrier] = None) -> bool:
    """Anhalten, auf flip_height_cm steigen, Flip in direction (f/b/l/r), zurück auf height_cm.

    barrier (Synchron-Flip): nach dem Steigen auf die anderen Drohnen warten, dann alle zugleich.
    Auch ohne Flip (Akku zu schwach, Steigen gescheitert) wird gewartet, damit die anderen nicht
    bis zum Zeitlimit hängen.

    Blockiert einige Sekunden (die SDK-Befehle warten auf "ok"); die Linienverfolgung pausiert
    so lange. Scheitert ein Schritt, fliegt die Drohne in der erreichten Höhe weiter – ein
    misslungener Flip ist kein Grund, den Flug abzubrechen. Rückgabe: Flip ausgeführt?
    """
    name = FLIP_NAMES[direction]
    tello.send_rc_control(0, 0, 0, 0)
    battery = tello.get_battery()
    if battery < FLIP_MIN_BATTERY:
        logger.warning(f"Kein Flip: Akku {battery} % – die Drohne flippt erst ab {FLIP_MIN_BATTERY} %.")
        sync_wait(barrier, logger)
        return False
    logger.info(f"Flip {name}{' (synchron)' if barrier is not None else ''}: steige auf {flip_height_cm} cm.")
    time.sleep(0.5)  # Schwung abbauen, bevor die Drohne steigt
    done = False
    # Keine Wiederholungen: djitellopy schickt einen Befehl ohne "ok" bis zu 3× erneut. Ist nur
    # die Antwort verloren gegangen, führt die Drohne ihn mehrfach aus – Flug 9.11. 13:38: "down 46"
    # ohne Antwort, danach 29 statt 50 cm Höhe und 21 s Blockade. Bei einem Flip hieße das
    # womöglich zwei Flips. Lieber einmal senden und mit der erreichten Höhe weitermachen.
    # Achtung: retry_count ist in djitellopy die Gesamtzahl der Versuche (range(retry_count)) –
    # 0 hieße, der Befehl wird gar nicht gesendet.
    retries, tello.retry_count = tello.retry_count, 1
    try:
        climb = flip_height_cm - tello.get_distance_tof()
        try:
            if climb >= MIN_STEP_CM:
                tello.move_up(min(climb, 500))
        except TelloException:
            sync_wait(barrier, logger)  # die anderen nicht warten lassen
            raise
        if not sync_wait(barrier, logger):
            raise TelloException("nicht alle Drohnen bereit")
        tello.flip(direction)
        done = True
        logger.info(f"Flip {name} ausgeführt.")
    except TelloException as error:
        logger.warning(f"Flip {name} fehlgeschlagen ({error}).")
    try:
        go_to_height(tello, height_cm, logger=logger, attempts=1)
    finally:
        tello.retry_count = retries
    return done
