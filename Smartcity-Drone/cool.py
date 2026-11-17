"""Drohne zwischen zwei Flügen abkühlen (Kühlmodus: Propeller drehen langsam, kein Abheben).

    python cool.py --drone 108             # bis 70 °C kühlen
    python cool.py --drone 108 --target 65
"""

from __future__ import annotations

import argparse
import logging

from djitellopy import Tello

from find_drones import find_by_serial, serial_for
from src.line_follower.classes.flight_error import FlightError
from src.line_follower.methods.cool_down import cool_down
from src.line_follower.utils import log


def main() -> None:
    p = argparse.ArgumentParser(description="Tello im Kühlmodus abkühlen.")
    p.add_argument("--drone", help="Name aus drones.json oder Seriennummer.")
    p.add_argument("--host", default="192.168.10.1", help="IP der Drohne, falls ohne --drone.")
    p.add_argument("--target", type=float, default=70, help="Zieltemperatur in °C.")
    args = p.parse_args()
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)
    log.setLevel(logging.INFO)
    log.propagate = False

    host = find_by_serial(serial_for(args.drone)) if args.drone else args.host
    tello = Tello(host=host)
    tello.connect()
    print(f"Akku {tello.get_battery()} %, Temperatur {tello.get_temperature():.0f} °C")
    if tello.get_temperature() <= args.target:
        print("Schon kühl genug.")
    else:
        try:
            cool_down(tello, args.target)
        except FlightError as error:
            tello.end()
            raise SystemExit(str(error)) from error
    print(f"Fertig: {tello.get_temperature():.0f} °C")
    tello.end()


if __name__ == "__main__":
    main()
