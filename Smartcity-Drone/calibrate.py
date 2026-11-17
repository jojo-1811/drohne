"""Ausrichtung der Downward-Kamera bestimmen (welches --rotate, gespiegelt?).

Die Drohne bleibt am Boden. Du hältst sie ca. 30-50 cm über einem Boden mit
etwas Struktur und schiebst sie auf Ansage langsam nach vorne und nach rechts.
Das Skript misst, in welche Richtung sich das Bild dabei bewegt.

    python calibrate.py --drone 108
"""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np
from djitellopy import Tello

from find_drones import find_by_serial, serial_for

# Erwartete Bildbewegung bei korrekter Ausrichtung (oben = vorne):
EXPECTED_FORWARD = np.array([0.0, 1.0])  # Drohne vor  -> Boden wandert im Bild nach unten
EXPECTED_RIGHT = np.array([-1.0, 0.0])  # Drohne rechts -> Boden wandert im Bild nach links

# Wie cv2.rotate einen Bewegungsvektor (dx, dy) dreht – passend zu --rotate in line_follower.py.
ROTATIONS = {
    0: lambda v: np.array([v[0], v[1]]),
    90: lambda v: np.array([-v[1], v[0]]),
    180: lambda v: np.array([-v[0], -v[1]]),
    270: lambda v: np.array([v[1], -v[0]]),
}


def measure_motion(frame_reader, seconds: float) -> np.ndarray:
    """Summierte Bildverschiebung (dx, dy) in Pixeln über die Messdauer."""
    total = np.zeros(2)
    prev = None
    window = None
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        gray = cv2.cvtColor(frame_reader.frame, cv2.COLOR_RGB2GRAY).astype(np.float32)
        if window is None:
            window = cv2.createHanningWindow(gray.shape[::-1], cv2.CV_32F)
        if prev is not None:
            (dx, dy), response = cv2.phaseCorrelate(prev, gray, window)
            if response > 0.05:  # nur verlässliche Messungen aufsummieren
                total += (dx, dy)
        prev = gray
        time.sleep(0.05)
    return total


def countdown(text: str) -> None:
    print(f"\n>>> {text}")
    for i in (3, 2, 1):
        print(f"    {i} ...", flush=True)
        time.sleep(1)
    print("    JETZT – langsam schieben (ca. 20 cm in 4 s)", flush=True)


def describe(v: np.ndarray) -> str:
    names = {(0, 1): "nach unten", (0, -1): "nach oben", (1, 0): "nach rechts", (-1, 0): "nach links"}
    axis = (int(np.sign(v[0])), 0) if abs(v[0]) > abs(v[1]) else (0, int(np.sign(v[1])))
    return f"{names.get(axis, '?')} ({v[0]:+.0f}, {v[1]:+.0f}) px"


def main() -> None:
    p = argparse.ArgumentParser(description="Ausrichtung der Downward-Kamera bestimmen.")
    p.add_argument("--host", default="192.168.10.1")
    p.add_argument("--drone", help="Name aus drones.json oder Seriennummer (statt --host).")
    args = p.parse_args()

    tello = Tello(host=find_by_serial(serial_for(args.drone)) if args.drone else args.host)
    tello.connect()
    tello.streamon()
    tello.set_video_direction(Tello.CAMERA_DOWNWARD)
    reader = tello.get_frame_read()
    time.sleep(2)
    try:
        print("Drohne waagerecht ca. 30-50 cm über den Boden halten, Vorderseite (Frontkamera) von dir weg.")
        countdown("Gleich: Drohne nach VORNE schieben")
        forward = measure_motion(reader, 4)
        print("    Stopp.")
        countdown("Gleich: Drohne nach RECHTS schieben (ohne sie zu drehen)")
        right = measure_motion(reader, 4)
        print("    Stopp.")
    finally:
        tello.set_video_direction(Tello.CAMERA_FORWARD)
        tello.streamoff()
        tello.end()

    print(f"\nBildbewegung bei 'vorne':  {describe(forward)}")
    print(f"Bildbewegung bei 'rechts': {describe(right)}")
    if np.linalg.norm(forward) < 15 or np.linalg.norm(right) < 15:
        print("\nZu wenig Bewegung erkannt – Boden mit mehr Struktur wählen oder weiter schieben und wiederholen.")
        return

    f, r = forward / np.linalg.norm(forward), right / np.linalg.norm(right)
    between = np.degrees(np.arccos(np.clip(abs(f @ r), 0, 1)))
    if between < 60:
        print(f"\n❌ Messung widersprüchlich: 'vorne' und 'rechts' müssten ~90° auseinanderliegen, "
              f"gemessen {between:.0f}°.\n   Drohne beim Schieben nicht kippen und nicht über die Linie halten – "
              "bitte wiederholen.")
        return
    rotate = max(ROTATIONS, key=lambda k: ROTATIONS[k](f) @ EXPECTED_FORWARD)
    mirrored = ROTATIONS[rotate](r) @ EXPECTED_RIGHT < 0
    flags = f"--rotate {rotate}" + (" --flip" if mirrored else "")
    print(f"\nErgebnis: bei line_follower.py immer  {flags}  angeben.")


if __name__ == "__main__":
    main()
