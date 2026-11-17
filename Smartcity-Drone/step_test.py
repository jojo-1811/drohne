"""Messflug: Wie reagiert die Drohne auf Seitenbefehle (lr)?

Die Drohne pendelt beim Linienfolgen seitlich (0,4–0,5 Hz). Um den Seitenregler
sauber einzustellen, müssen Verzögerung, Totzone und Geschwindigkeit pro
RC-Einheit bekannt sein. Aus normalen Flügen lässt sich das nicht ablesen, weil
der Regler dort ständig gegensteuert.

Ablauf: abheben, schweben, dann für jede Stärke einen kurzen Seitenstoß nach
rechts und einen nach links geben, dazwischen schweben. Gemessen wird die
Bewegung des Bodens im Kamerabild (Phasenkorrelation, wie in calibrate.py) –
eine Linie muss dafür nicht im Bild sein, der Boden braucht nur etwas Struktur.

    python step_test.py --drone 108

Platz: mindestens 1 m frei zu beiden Seiten. Abbruch jederzeit mit q im Videofenster.
Ergebnis: recordings/<Zeit>_sprungtest/step.csv und raw.mp4
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from djitellopy import Tello

from find_drones import find_by_serial, serial_for
from src.line_follower.methods.preprocess import preprocess
from src.line_follower.methods.start_video import start_video

HOVER_BEFORE = 3.0  # Sekunden schweben vor dem ersten Stoß (Ruhe-Rauschen messen)


def schedule(amplitudes: list[int], pulse: float, pause: float) -> list[tuple[float, float, int]]:
    """(Beginn, Ende, lr) aller Stöße, abwechselnd rechts und links, damit die Drohne am Ort bleibt."""
    steps, t = [], HOVER_BEFORE
    for amplitude in amplitudes:
        for sign in (1, -1):
            steps.append((t, t + pulse, sign * amplitude))
            t += pulse + pause
    return steps


def command_at(steps: list[tuple[float, float, int]], t: float) -> int:
    return next((lr for start, end, lr in steps if start <= t < end), 0)


def summarize(rows: list[dict], steps: list[tuple[float, float, int]]) -> None:
    """Pro Stoß: Verzögerung bis zur Bewegung, höchste Geschwindigkeit, zurückgelegter Weg (Bildpixel)."""
    t = np.array([r["t"] for r in rows])
    speed = np.array([r["speed_px_s"] for r in rows])  # + = Drohne bewegt sich nach rechts
    calm = speed[t < HOVER_BEFORE]
    threshold = max(3 * float(np.std(calm)) if len(calm) > 5 else 0.0, 5.0)
    print(f"\nRuhe-Rauschen: {np.std(calm) if len(calm) else float('nan'):.1f} px/s, Bewegungsschwelle {threshold:.1f} px/s")
    print(" lr   Verzögerung  max. Geschw.  Weg")
    for start, end, lr in steps:
        window = (t >= start) & (t < end + 1.5)
        if not window.any():
            continue
        directed = np.sign(lr) * speed[window]
        moving = np.nonzero(directed > threshold)[0]
        delay = f"{t[window][moving[0]] - start:5.2f} s" if len(moving) else "  keine"
        distance = float(np.sum(directed * np.diff(t[window], prepend=t[window][0])))
        print(f"{lr:+4d}   {delay:>9}   {directed.max():8.0f} px/s  {distance:5.0f} px")


def main() -> None:
    p = argparse.ArgumentParser(description="Messflug: Reaktion der Drohne auf Seitenbefehle.")
    p.add_argument("--host", default="192.168.10.1")
    p.add_argument("--drone", help="Name aus drones.json oder Seriennummer.")
    p.add_argument("--amplitudes", type=int, nargs="+", default=[10, 15, 20, 30],
                   help="Stärken der Seitenstöße (RC-Einheiten).")
    p.add_argument("--pulse", type=float, default=1.0, help="Dauer eines Stoßes (s).")
    p.add_argument("--pause", type=float, default=2.5, help="Schweben zwischen zwei Stößen (s).")
    p.add_argument("--rotate", type=int, default=90, choices=[0, 90, 180, 270])
    p.add_argument("--flip", action="store_true")
    p.add_argument("--min-battery", type=int, default=30)
    args = p.parse_args()

    host = find_by_serial(serial_for(args.drone)) if args.drone else args.host
    tello = Tello(host=host)
    tello.connect()
    battery = tello.get_battery()
    print(f"Akku: {battery} %, Temperatur: {tello.get_temperature():.0f} °C")
    if battery < args.min_battery:
        raise SystemExit(f"Akku unter {args.min_battery} % – nicht starten.")

    steps = schedule(args.amplitudes, args.pulse, args.pause)
    out_dir = Path(__file__).parent / "recordings" / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_sprungtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    reader = start_video(tello)
    time.sleep(2)

    rows: list[dict] = []
    video = None
    flying = False
    try:
        tello.takeoff()
        flying = True
        t0 = time.monotonic()
        last_frame, previous, last_t = None, None, None
        window = None
        while True:
            now = time.monotonic() - t0
            if now > steps[-1][1] + args.pause:
                break
            frame = reader.frame
            if frame is None or frame is last_frame:
                if cv2.waitKey(2) & 0xFF == ord("q"):
                    break
                continue
            last_frame = frame
            gray = preprocess(frame, args.rotate, args.flip)
            lr = command_at(steps, now)
            tello.send_rc_control(lr, 0, 0, 0)

            current = np.float32(gray)
            if window is None:
                window = cv2.createHanningWindow(current.shape[::-1], cv2.CV_32F)
            speed = 0.0
            if previous is not None and now > last_t:
                (dx, _), _ = cv2.phaseCorrelate(previous, current, window)
                # Bewegt sich die Drohne nach rechts, wandert der Boden im Bild nach links.
                speed = -dx / (now - last_t)
            previous, last_t = current, now

            rows.append({"t": round(now, 3), "lr": lr, "speed_px_s": round(speed, 1),
                         "vgy": tello.get_speed_y(), "roll": tello.get_roll(), "height_cm": tello.get_distance_tof()})
            if video is None:
                video = cv2.VideoWriter(str(out_dir / "raw.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 20.0,
                                        (gray.shape[1], gray.shape[0]))
            video.write(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
            view = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            cv2.putText(view, f"t {now:5.1f}s  lr {lr:+d}  {speed:+.0f} px/s", (5, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
            cv2.imshow("Sprungtest", view)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        if flying:
            tello.send_rc_control(0, 0, 0, 0)
            tello.land()
        reader.stop()
        tello.streamoff()
        tello.end()
        if video is not None:
            video.release()
        cv2.destroyAllWindows()
        if rows:
            with open(out_dir / "step.csv", "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Messung gespeichert: {out_dir}")
            summarize(rows, steps)


if __name__ == "__main__":
    main()
