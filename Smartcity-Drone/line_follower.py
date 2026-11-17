"""Linienverfolgung für RoboMaster TT (Tello Talent) mit der Downward-Kamera.

Ablauf:
    1. Mit dem WLAN der Drohne verbinden (RMTT-xxxxxx).
    2. Erst im Testmodus prüfen, ob die Linie sauber erkannt wird:
           python line_follower.py --dry-run
       (Drohne bleibt am Boden, Drohne von Hand über die Linie halten.)
    3. Dann fliegen:
           python line_follower.py

Vorher einmal die Kameraausrichtung bestimmen: python calibrate.py

Tasten im Videofenster:  q = landen und beenden
"""

from __future__ import annotations

import argparse
import logging
from typing import Optional

from find_drones import find_by_serial, serial_for
from src.line_follower.classes.flight_error import FlightError
from src.line_follower.classes.frontend import WindowFrontend
from src.line_follower.methods.fly import fly
from src.line_follower.utils import log


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RoboMaster TT folgt einer Linie (Downward-Kamera).")
    p.add_argument("--host", default="192.168.10.1",
                   help="IP der Drohne (Standard: eigenes WLAN der Drohne). Finden mit find_drones.py.")
    p.add_argument("--drone", help="Drohne über Namen aus drones.json (z. B. 108) oder Seriennummer ansprechen; "
                                   "die aktuelle IP wird im Netz gesucht. Ersetzt --host.")
    p.add_argument("--dry-run", action="store_true", help="Nicht abheben, nur Erkennung anzeigen.")
    # Messlauf am Boden 2.11. (Motoren aus): Videostream an +2,2 °C/min (65 -> 76,5 °C in 4 min),
    # wieder aus -1,7 °C/min; Mission-Pad-Erkennung allein +0,5 statt +0,3 °C/min. Im Flug kühlt
    # der Luftstrom, am Boden heizt der Stream – deshalb den Testmodus begrenzen.
    p.add_argument("--test-limit", type=float, default=180,
                   help="Testmodus nach so vielen Sekunden beenden (Videostream heizt am Boden, 0 = unbegrenzt).")
    p.add_argument("--light-line", action="store_true", help="Helle Linie auf dunklem Boden (Standard: dunkle Linie).")
    p.add_argument("--threshold", type=int, default=None, help="Fester Graustufen-Schwellwert 0-255 (Standard: adaptiv).")
    p.add_argument("--max-line-width", type=float, default=0.2,
                   help="Breitere dunkle Flächen (Mission Pad, Roboter) ignorieren – Anteil der Bildbreite.")
    p.add_argument("--dashed", action=argparse.BooleanOptionalAction, default=True,
                   help="Gestrichelte Linie: Lücken zwischen Strichen überbrücken (Standard; schadet auf "
                        "durchgezogenen Linien nicht, nachgespielt mit den Flügen vom 16.11.).")
    p.add_argument("--max-gap", type=float, default=0.25,
                   help="Mit --dashed: größte Lücke zwischen zwei Strichen als Anteil der Bildbreite.")
    p.add_argument("--contrast", type=int, default=25,
                   help="Adaptiver Modus: so viel dunkler (bzw. heller) als die Umgebung muss die Linie sein.")
    p.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=90,
                   help="Bild drehen, sodass 'oben' = Flugrichtung vorne. Standard 90 = RMTT (mit Papier-Pfeil geprüft).")
    p.add_argument("--flip", action="store_true", help="Bild spiegeln (wenn calibrate.py das meldet).")
    p.add_argument("--record", action="store_true", help="Flug als Video + CSV unter recordings/ speichern.")
    p.add_argument("--height", type=int, default=55,
                   help="Flughöhe in cm (40–100; die Drohne hebt auf ~80 cm ab und steigt/sinkt dann). "
                        "Die Seitenregler-Werte gelten für 55 cm und werden mit der Höhe skaliert.")
    p.add_argument("--speed", type=int, default=25,
                   help="Vorwärtsgeschwindigkeit (RC 0-100). Unter ~20 bewegt sich die Tello kaum.")
    # Seitenregler aus dem Sprungtest vom 26.10. (step_test.py, 80 cm Höhe): Die Drohne
    # reagiert erst nach ~0,9 s auf einen Seitenbefehl und bewegt sich dann ~3,6 px pro
    # RC-Einheit und Sekunde (bei 55 cm Flughöhe ~5,2 px, also ~0,044 Versatz). Für 45°
    # Phasenreserve bei 0,9 s Totzeit darf der Regelkreis höchstens bei ~0,87 rad/s
    # durchschneiden -> P ≈ 0,87 / 0,044 ≈ 20. Mit 35 lag die Reserve bei nur ~10°: Die
    # Drohne pendelte mit 0,4–0,5 Hz. D-Anteil im gleichen Verhältnis (0,43 s) wie zuvor.
    p.add_argument("--k-lateral", type=float, default=20.0)
    p.add_argument("--k-lateral-d", type=float, default=9.0, help="D-Anteil Seitenregelung (Dämpfung).")
    # Im Vorwärtsflug driftet die Drohne gleichmäßig nach rechts (Schweben im Sprungtest: keine
    # Drift). Zum Ausgleich brauchte sie in allen Flügen vom 26.10. im Mittel lr -2 bis -7. Der
    # P-Anteil allein liefert das nur mit bleibendem Versatz: Mit P 20 lag die Linie auf geraden
    # Stücken im Mittel bei -0,3 (Flüge 12:48, 12:50). Der I-Anteil baut diesen Ausgleich langsam
    # auf. Nachstellzeit P/I = 4 s, also etwa 4 × Totzeit – langsam genug, um nicht aufzuschaukeln
    # (mit P 35 und Totzonen-Ausgleich hatte ein I-Anteil in einem früheren Flugtest Pendeln verursacht).
    p.add_argument("--k-lateral-i", type=float, default=5.0,
                   help="I-Anteil Seitenregelung gegen gleichmäßige Seitendrift (0 = aus).")
    # In Kurven trägt der Schwung die Drohne nach außen, der P-Anteil reagiert darauf zu spät
    # (Flüge 9.11.: Linie bis 0,33 innen, Seitenbefehl ~0). Die Vorsteuerung lenkt abhängig von
    # Kurvenwinkel und Vorwärtsfahrt vorab nach innen, siehe LineController.curve_feedforward.
    # 20 war im Flug 12:16 (50 cm) zu stark: Die Linie lag in Kurven im Mittel 0,1–0,3 außen.
    p.add_argument("--k-lateral-far", type=float, default=0.0,
                   help="Zusätzlicher Seitenanteil, wenn die Linie weiter als 0,35 außen liegt (je 0,1 mehr "
                        "Versatz 3,5 mehr Seitenbefehl bei 35); dabei auch langsamer vorwärts (bis 40 %%). "
                        "Standard 0 = aus: Mit 35 drückte es Drohne 109 am 16.11. (13:45) an einer Ecke mit "
                        "Seitwärts -100 von der Linie – dort ist der Versatz mehrdeutig.")
    p.add_argument("--k-curve", type=float, default=12.0,
                   help="Kurven-Vorsteuerung: seitwärts in die Kurve steuern (Standard 12, 0 = aus).")
    p.add_argument("--k-yaw", type=float, default=0.8)
    p.add_argument("--k-yaw-d", type=float, default=0.05)
    p.add_argument("--min-area", type=float, default=0.01, help="Min. Linienfläche als Anteil des Bildes.")
    p.add_argument("--max-area", type=float, default=0.4, help="Max. Linienfläche als Anteil des Bildes.")
    p.add_argument("--lost-timeout", type=float, default=2.0, help="Sekunden ohne Linie bis zur Landung.")
    p.add_argument("--min-rc", type=int, default=15,
                   help="Totzonen-Ausgleich: kleinster wirksamer RC-Wert für Drehung und Vor/Zurück (0 = aus).")
    # Seitwärts reagiert die Drohne schon auf 10 (Sprungtest 26.10.: ~25 px Weg bei einem 1-s-Stoß,
    # ~linear bis 30). Der Ausgleich auf 15 machte aus jeder winzigen Korrektur einen vollen Stoß –
    # zusammen mit der Totzeit ein Pendeln.
    p.add_argument("--min-rc-lateral", type=int, default=0,
                   help="Totzonen-Ausgleich seitwärts (Standard 0 = aus; vorher galt --min-rc auch hier).")
    p.add_argument("--min-battery", type=int, default=25, help="Mindest-Akku zum Starten (%%).")
    p.add_argument("--max-temp", type=int, default=80,
                   help="Nicht starten, wenn die Drohne mindestens so heiß ist (°C).")
    p.add_argument("--cool", action="store_true",
                   help="Ist die Drohne zu heiß, vor dem Start im Kühlmodus (Propeller drehen, kein Abheben) abkühlen.")
    p.add_argument("--corrupt-threshold", type=float, default=50.0,
                   help="Bilder mit mittlerer Kantenstärke darüber gelten als zerstört (normal 15–30, "
                        "Nahaufnahme eines Roboters bis ~40, zerstört meist 60–85).")
    p.add_argument("--video-restarts", type=int, default=3,
                   help="So oft wird der Videostream pro Flug bei Bildfehlern höchstens neu gestartet.")
    p.add_argument("--video-timeout", type=float, default=8.0,
                   help="Sekunden ohne brauchbares Bild (trotz Neustarts), bevor die Drohne landet.")
    p.add_argument("--junction", choices=["ask", "auto"], default="ask",
                   help="An Abzweigungen: ask = anhalten und per Taste fragen (a/w/d), "
                        "auto = Ast nehmen, der am wenigsten von geradeaus abweicht.")
    p.add_argument("--junction-timeout", type=float, default=30.0,
                   help="Sekunden, die die Drohne an einer Abzweigung auf eine Antwort wartet, bevor sie landet.")
    p.add_argument("--cars", action=argparse.BooleanOptionalAction, default=False,
                   help="Vor einem Auto auf der Strecke anhalten und warten, bis es weg ist. Standard (--no-cars): "
                        "einfach darüber hinwegfliegen – verdeckt das Auto die Linie, geradeaus weiter.")
    p.add_argument("--overfly-timeout", type=float, default=5.0,
                   help="So lange fliegt die Drohne über einem Auto ohne sichtbare Linie geradeaus weiter.")
    p.add_argument("--car-timeout", type=float, default=30.0,
                   help="Sekunden, die die Drohne vor einem Auto wartet, bevor sie landet.")
    p.add_argument("--height-hold", action=argparse.BooleanOptionalAction, default=True,
                   help="Flughöhe während des Flugs nachregeln (z. B. nachdem ein Auto unter der Drohne war).")
    p.add_argument("--pad-landing", action=argparse.BooleanOptionalAction, default=True,
                   help="Auf dem Mission Pad am Streckenende landen (Start-Pad zählt nicht).")
    p.add_argument("--land-battery", type=int, default=15, help="Im Flug bei diesem Akkustand landen (%%).")
    p.add_argument("--random", action="store_true", help="Drohne wählt einen zufälligen Ast an einer Kreuzung.")
    p.add_argument("--search-start", action="store_true",
                   help="Start neben der Linie: nach dem Abheben auf --search-height steigen, die nächste Linie "
                        "suchen, darüber stellen, in ihre Richtung drehen und auf Flughöhe sinken.")
    p.add_argument("--search-height", type=int, default=100,
                   help="Höhe in cm für die Linien-Suche (aus 100 cm sieht die Kamera etwa 1,6 × 1,2 m Boden).")
    p.add_argument("--drone-guard", action="store_true",
                   help="Ist etwas unter der Drohne (Abstandssensor fällt deutlich unter die Flughöhe, z. B. eine "
                        "tiefer fliegende Drohne): anhalten, auf --guard-height steigen, warten bis frei, zurück.")
    p.add_argument("--guard-height", type=int, default=130, help="Ausweichhöhe in cm für --drone-guard.")
    p.add_argument("--flip-height", type=int, default=100,
                   help="Höhe in cm, auf die die Drohne vor einem Flip steigt (ein Flip kostet 20–30 cm).")
    p.add_argument("--rescue-searches", type=int, default=2,
                   help="So oft pro Flug nach Linienverlust von oben suchen (--search-height, --search-radius), "
                        "statt zu landen. 0 = gleich landen.")
    p.add_argument("--search-radius", type=int, default=50,
                   help="Ist von oben keine Linie zu sehen: Quadrat mit diesem Abstand in cm um den Start abfliegen.")
    # Bildrate (setfps) bewusst nicht einstellbar: Schon "middle" halbierte am 2.11. die Zahl
    # verschiedener Bilder der unteren Kamera (~11 -> ~6 pro s) – der Regler sähe die Linie
    # nur halb so oft, bei ohnehin 0,9 s Verzögerung.
    p.add_argument("--video-bitrate", type=int, choices=range(0, 6), default=0,
                   help="Videobitrate in Mbit/s (1–5, weniger = weniger Datenmenge), 0 = automatisch.")
    args = p.parse_args(argv)
    if not 40 <= args.height <= 100:
        p.error("--height muss zwischen 40 und 100 cm liegen.")
    if not 50 <= args.search_height <= 150:
        p.error("--search-height muss zwischen 50 und 150 cm liegen.")
    if not 60 <= args.guard_height <= 200:
        p.error("--guard-height muss zwischen 60 und 200 cm liegen.")
    if not 20 <= args.search_radius <= 100:
        p.error("--search-radius muss zwischen 20 und 100 cm liegen.")
    return args


def main() -> None:
    args = parse_args()
    # Nur den eigenen Logger einrichten: basicConfig am Root-Logger würde die Meldungen von
    # djitellopy (eigener Handler) ein zweites Mal ausgeben.
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)
    log.setLevel(logging.INFO)
    log.propagate = False

    host = args.host
    if args.drone:
        serial = serial_for(args.drone)
        host = find_by_serial(serial)
        log.info(f"Drohne {args.drone} ({serial}) gefunden unter {host}")
    frontend = WindowFrontend()
    try:
        fly(args, host, frontend)
    except FlightError as error:
        raise SystemExit(1 if error.logged else str(error)) from error
    finally:
        frontend.close()


if __name__ == "__main__":
    main()
