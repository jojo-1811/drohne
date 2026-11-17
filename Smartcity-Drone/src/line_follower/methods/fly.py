from __future__ import annotations

import argparse
import logging
import random
import time
from typing import Optional

import numpy as np
from djitellopy import Tello, TelloException

from src.line_follower.classes.car_guard import CarGuard
from src.line_follower.classes.drone_guard import DroneGuard
from src.line_follower.classes.flight_error import FlightError
from src.line_follower.classes.flight_recorder import FlightRecorder
from src.line_follower.classes.flight_state import FlightState
from src.line_follower.classes.frontend import Frontend
from src.line_follower.classes.gains import Gains
from src.line_follower.classes.height_hold import HeightHold
from src.line_follower.classes.junction_guide import JunctionGuide
from src.line_follower.classes.line_controller import LineController
from src.line_follower.classes.line_tracker import LineTracker
from src.line_follower.classes.pad_watcher import PadWatcher
from src.line_follower.classes.video_guard import VideoGuard
from src.line_follower.classes.video_reader import VideoReader
from src.line_follower.classes.wide_objects import WideObjects
from src.line_follower.methods._cleanup import _cleanup
from src.line_follower.methods.bridge_dashes import bridge_dashes
from src.line_follower.methods.car_ahead import car_ahead
from src.line_follower.methods.cool_down import cool_down
from src.line_follower.methods.do_flip import FLIP_KEYS, FLIP_NAMES, SIDE_SEARCH_RC, SIDE_SEARCH_S, do_flip, sync_wait
from src.line_follower.methods.draw_overlay import draw_overlay
from src.line_follower.methods.find_wide_objects import find_wide_objects
from src.line_follower.methods.go_to_height import go_to_height
from src.line_follower.methods.land_on_pad import land_on_pad
from src.line_follower.methods.measure_line import measure_line
from src.line_follower.methods.preprocess import preprocess
from src.line_follower.methods.remove_blobs import remove_blobs
from src.line_follower.methods.restart_video import restart_video
from src.line_follower.methods.search_line import search_line
from src.line_follower.methods.segment_line import segment_line
from src.line_follower.methods.start_video import start_video
from src.line_follower.utils import (DOWNWARD_FRAME_SHAPE, FRAME_TIMEOUT, RECORDINGS_DIR, REFERENCE_HEIGHT_CM, SUCCESS,
                                    is_corrupt, log)


def fly(args: argparse.Namespace, host: str, frontend: Frontend, logger: logging.Logger = log,
        video_port: int = Tello.VS_UDP_PORT, name: str = "") -> None:
    """Einen Flug durchführen: verbinden, abheben, der Linie folgen, landen.

    video_port: Port, an den die Drohne ihr Video schickt. Fliegen mehrere Drohnen
    gleichzeitig, braucht jede einen eigenen (SDK-Befehl "port"); der Statusport
    8890 bleibt, djitellopy ordnet die Statuspakete nach Absender-IP zu.
    """
    tracker = LineTracker()
    junctions = JunctionGuide() if args.junction == "ask" else None
    cars = CarGuard() if args.cars else None
    # Die Seitenregler-Werte gelten für REFERENCE_HEIGHT_CM. Höher sieht die Kamera mehr Boden,
    # dieselbe Seitenbewegung verschiebt die Linie im Bild weniger (Versatz ~ 1/Höhe) – mit
    # height/REFERENCE_HEIGHT_CM skaliert bleibt die Regelung gleich schnell und gleich ruhig.
    # Drehung braucht das nicht: Winkel im Bild hängen nicht von der Höhe ab.
    # Oberhalb der Bezugshöhe nur mit der Wurzel skalieren: Auf 100 cm pendelte die Drohne mit voller
    # Skalierung (×1,8) doppelt so oft um die Linie wie auf 50 cm (Flug 16.11. 10:28) – das dünnere
    # Band verrauscht den Versatz, der D-Anteil verstärkt das.
    ratio = args.height / REFERENCE_HEIGHT_CM
    scale = (ratio if ratio <= 1 else ratio ** 0.5) if not args.dry_run else 1.0
    # Aus größerer Höhe ist das Band schmaler und Linienstücke kleiner (10:28 auf 100 cm: 0,8 % bei
    # 1 % Mindestfläche) – Mindestfläche mit der Höhe verkleinern.
    min_area = args.min_area * min(1.0, REFERENCE_HEIGHT_CM / args.height)
    controller = LineController(Gains(args.speed, args.k_lateral * scale, args.k_lateral_d * scale,
                                      args.k_lateral_i * scale, args.k_yaw, args.k_yaw_d, args.min_rc,
                                      args.min_rc_lateral, args.k_curve, args.k_lateral_far * scale))
    state = FlightState()
    frontend.show(None, state)

    tello = Tello(host=host)
    tello.connect()
    if video_port != Tello.VS_UDP_PORT:
        try:
            tello.change_vs_udp(video_port)
        except TelloException as error:
            tello.end()
            raise FlightError(f"Drohne kann den Videoport nicht wechseln ({error}) – "
                              "dann fliegt nur eine Drohne gleichzeitig.") from error
    battery = tello.get_battery()
    temperature = tello.get_temperature()
    logger.info(f"Akku: {battery} %, Temperatur: {temperature:.0f} °C")
    state.battery, state.temp_c = battery, temperature
    frontend.show(None, state)
    if not args.dry_run and battery < args.min_battery:
        tello.end()
        raise FlightError(f"Akku unter {args.min_battery} % – nicht starten.")
    # Ab ~85 °C fielen im Test Video und Verbindung aus; die Tello schaltet sich dann selbst ab.
    if not args.dry_run and temperature >= args.max_temp:
        if not args.cool:
            tello.end()
            raise FlightError(f"Drohne zu heiß ({temperature:.0f} °C, Grenze {args.max_temp} °C) – erst kühlen "
                              "(Webinterface: ❄ Kühlen, Terminal: cool.py oder --cool) oder ausschalten und abkühlen lassen.")
        cool_down(tello, args.max_temp - 5, logger=logger)

    pad_watcher = None
    if args.pad_landing:
        try:
            tello.enable_mission_pads()
            tello.set_mission_pad_detection_direction(0)  # nur nach unten -> 20 Hz
            pad_watcher = PadWatcher()
        except TelloException as error:
            logger.warning(f"Mission-Pad-Erkennung nicht verfügbar ({error}) – Pad-Landung aus.")

    state.status = "KAMERA STARTET"
    frontend.show(None, state)
    try:
        frame_reader: Optional[VideoReader] = start_video(tello, logger=logger, bitrate=args.video_bitrate)
    except TelloException as error:
        tello.end()
        raise FlightError(str(error)) from error
    time.sleep(2)  # Stream nach dem Kamerawechsel stabilisieren lassen

    recorder = FlightRecorder(RECORDINGS_DIR, logger=logger, name=name) if args.record else None
    flying = False

    def publish(gray: np.ndarray, view: np.ndarray, status: str, command: tuple[int, int, int]) -> None:
        """Während der Linien-Suche: Bild aufzeichnen/anzeigen, "Landen" abfragen."""
        if recorder:
            recorder.write(gray.shape, gray, view, None, command, status, tello, False,
                           frame_reader.decoder_corrupt)
        state.status, state.flying, state.prompt = status, True, ()
        frontend.show(view, state)
        if frontend.poll_key(1) == "q":
            raise FlightError("Landung während der Linien-Suche angefordert.")

    try:
        if not args.dry_run:
            state.status = "STARTE"
            frontend.show(None, state)
            # Vor dem Befehl als fliegend markieren: Kommt die Antwort nicht an (Flug 2.11. 14:45:
            # Abheben dauerte 21 s), ist sie womöglich trotzdem in der Luft – dann muss der
            # finally-Block landen. Am Boden schadet der Landebefehl nicht.
            flying = True
            tello.takeoff()
            logger.log(SUCCESS, "Abgehoben.")
            if args.search_start:
                search_line(tello, frame_reader, args, publish, logger=logger)
            else:
                go_to_height(tello, args.height, logger=logger)

        last_seen = last_frame_time = last_good_frame = loop_start = time.monotonic()
        rescues = 0  # Suchflüge nach Linienverlust
        last_frame = None
        video_guard = VideoGuard(args.video_restarts)
        pending_key: Optional[str] = None
        lr = fb = yaw = 0  # zuletzt gesendeter Befehl
        objects: Optional[WideObjects] = None
        height_hold = HeightHold(args.height) if args.height_hold else None
        drone_guard = DroneGuard(args.height, args.guard_height) if args.drone_guard and not args.dry_run else None
        guard_frame = None  # zuletzt beim Ausweichen aufgezeichnetes Bild
        overfly_since: Optional[float] = None  # seit wann ein Auto die Linie verdeckt (Überfliegen)

        def guard_tick(status: str) -> Optional[str]:
            """Während des Ausweichens: neues Bild anzeigen/aufzeichnen, Taste zurückgeben."""
            nonlocal guard_frame
            frame_now = frame_reader.frame if frame_reader is not None else None
            view_now = None
            if frame_now is not None and frame_now is not guard_frame:
                guard_frame = frame_now
                gray_now = preprocess(frame_now, args.rotate, args.flip)
                view_now = draw_overlay(gray_now, np.zeros_like(gray_now), None, status)
                if recorder:
                    recorder.write(frame_now.shape, gray_now, view_now, None, (0, 0, 0), status, tello, False,
                                   frame_reader.decoder_corrupt)
            state.status, state.flying, state.prompt = status, True, ()
            state.height_cm = tello.get_distance_tof()
            frontend.show(view_now, state)
            return frontend.poll_key(1)
        search_lr, search_until = 0, 0.0  # nach einem Seiten-Flip zurück zur Linie (siehe do_flip)
        search_from = 0.0  # Bilder davor können noch von vor dem Flip stammen
        last_tick: Optional[float] = None  # Zeit des zuletzt ausgewerteten Schleifendurchlaufs
        while True:
            # Jedes Kamerabild genau einmal auswerten und genau einen RC-Befehl pro Bild senden.
            # Ohne diese Synchronisation läuft die Schleife tausendfach pro Sekunde über
            # dasselbe Bild und flutet die Drohne mit RC-Befehlen.
            frame = frame_reader.frame if frame_reader is not None else None
            now = time.monotonic()
            video_stalled = corrupt = False
            if frame is not None and frame is not last_frame:
                last_frame, last_frame_time = frame, now
                gray = preprocess(frame, args.rotate, args.flip)
                # Falsches Format (siehe start_video) wie ein zerstörtes Bild behandeln -> Neustart.
                corrupt = frame.shape[:2] != DOWNWARD_FRAME_SHAPE or is_corrupt(gray, args.corrupt_threshold)
                video_guard.add(corrupt, now)
                if corrupt:
                    # Im zerstörten Bild "erkannte" Linien sind Zufall – nicht danach steuern.
                    mask = np.zeros_like(gray)
                    objects = None
                    line = None
                else:
                    last_good_frame = now
                    mask = segment_line(gray, line_is_dark=not args.light_line, threshold=args.threshold,
                                        contrast=args.contrast)
                    objects = find_wide_objects(gray, not args.light_line, args.max_line_width, args.contrast)
                    mask = remove_blobs(mask, objects, args.max_line_width)
                    if args.dashed:
                        mask = bridge_dashes(mask, args.max_gap)
                    line = measure_line(mask, min_area, args.max_area, tracker)
            elif now - last_frame_time < FRAME_TIMEOUT:
                # Auf das nächste Bild warten. Tasten hier mit einlesen – die Schleife verbringt
                # die meiste Zeit in diesem Zweig, sonst gingen fast alle Tastendrücke verloren.
                pending_key = frontend.poll_key(2) or pending_key
                if pending_key == "q":
                    logger.info("Landung angefordert.")
                    break
                continue
            else:
                # Videostream steht (z. B. Verbindung zur Drohne weg): Es gibt keine aktuelle
                # Information über die Linie mehr. Befehle dabei mit ~20 Hz statt im Bildtakt.
                video_stalled = True
                line = None
                objects = None
                pending_key = frontend.poll_key(50) or pending_key

            if video_guard.should_restart(now, last_frame_time):
                if flying:
                    tello.send_rc_control(0, 0, 0, 0)  # schweben, solange kein Bild da ist
                logger.warning(f"Videostörung – starte Kamera neu ({video_guard.restarts + 1}/{video_guard.max_restarts}).")
                # Schlägt der Neustart fehl, schwebt die Drohne ohne Stream weiter: VideoGuard
                # versucht es nach der Pause erneut, nach --video-timeout ohne Bild wird gelandet.
                frame_reader = restart_video(tello, frame_reader, logger=logger, bitrate=args.video_bitrate)
                video_guard.restarted(time.monotonic())
                last_frame, last_frame_time = None, time.monotonic()
                tracker.reset()
                continue

            if junctions is not None:
                was_waiting = junctions.waiting
                junctions.update(line, now, fb)
                tracker.keep_direction = junctions.passing
                if junctions.waiting and not was_waiting:
                    logger.info(f"Abzweigung erkannt – bitte wählen: {junctions.prompt()}")
            if cars is not None:
                was_waiting = cars.waiting
                cars.update(car_ahead(objects.cars, line) if objects is not None and line is not None else None, now,
                            blind=line is None and objects is not None and bool(objects.cars.any()))
                if cars.waiting and not was_waiting:
                    logger.info("Auto auf der Strecke – warte, bis es weg ist.")
                elif was_waiting and not cars.waiting:
                    logger.info("Strecke frei – fliege weiter.")
            if line is not None:
                last_seen = time.monotonic()
                if now >= search_from:
                    search_until = 0.0  # Linie wieder da – Suche nach dem Seiten-Flip beendet
                lr, fb, yaw = controller.update(line)
                if junctions is not None and junctions.waiting:
                    # Vor der Kreuzung stehen bleiben: seitlich über der Linie, vor/zurück so,
                    # dass die Kreuzung knapp vor der Drohne im Bild bleibt.
                    fb = controller.out(junctions.hold_forward(line, gray.shape[0], now))
                    yaw = 0
                    status = f"ABZWEIGUNG – WARTE {now - junctions.asked_at:.0f}s fb {fb:+d}"
                elif junctions is not None and junctions.approaching:
                    fb = int(round(fb * JunctionGuide.APPROACH_SPEED))
                    status = f"ABZWEIGUNG IN SICHT – LANGSAM lr {lr:+d} fb {fb:+d} yaw {yaw:+d}"
                else:
                    status = f"FOLGEN lr {lr:+d} fb {fb:+d} yaw {yaw:+d}"
                if cars is not None and cars.waiting:
                    fb = min(fb, controller.out(cars.hold_forward()))
                    status = f"AUTO – WARTE {now - cars.since:.0f}s fb {fb:+d}"
            elif video_stalled or corrupt:
                # Blind: auf der Stelle schweben (die Tello hält die Position selbst) und dem
                # Video Zeit für einen Neustart geben. Die Blindzeit zählt nicht als Linienverlust,
                # setzt den Timer aber auch nicht zurück, sondern hält ihn nur an – sonst verlängerte
                # jeder kurze Aussetzer die Suche ohne Linie (Flug 09.11. 11:41: 10 s statt 2 s).
                controller.reset()
                lr = fb = yaw = 0
                if last_tick is not None:
                    last_seen = min(last_seen + (now - last_tick), now)
                blind_for = now - last_good_frame
                status = f"{'KEIN VIDEO' if video_stalled else 'BILDFEHLER'} {blind_for:.1f}s"
                if flying and blind_for > args.video_timeout:
                    logger.warning("Kein brauchbares Videobild mehr – lande.")
                    break
            elif cars is not None and cars.waiting:
                # Auto verdeckt die Linie (steht auf ihr): auf der Stelle schweben, nicht als
                # Linienverlust zählen – sonst landete die Drohne womöglich auf dem Auto.
                controller.reset()
                lr = fb = yaw = 0
                last_seen = time.monotonic()
                status = f"AUTO – WARTE {now - cars.since:.0f}s (Linie verdeckt)"
            elif (cars is None and objects is not None and bool(objects.cars.any())
                  and (overfly_since is None or now - overfly_since <= args.overfly_timeout)):
                # Über ein Auto hinweg: es verdeckt die Linie -> geradeaus weiterfliegen, bis sie
                # dahinter wieder auftaucht. Zählt nicht als Linienverlust. Die Höhenregelung pausiert
                # über dem Auto ohnehin (der Sensor misst das Dach).
                overfly_since = overfly_since or now
                controller.reset()
                lr, fb, yaw = 0, args.speed, 0
                last_seen = time.monotonic()
                status = f"ÜBER AUTO {now - overfly_since:.1f}s fb {fb:+d}"
            elif now < search_until:
                # Nach einem Flip zur Seite: in die Gegenrichtung schieben, bis die Linie wieder im Bild ist.
                controller.reset()
                lr, fb, yaw = search_lr, 0, 0
                last_seen = time.monotonic()
                status = f"ZURÜCK ZUR LINIE lr {lr:+d}"
            else:
                controller.reset()
                lr = fb = yaw = 0
                lost_for = time.monotonic() - last_seen
                if lost_for > 0.5:
                    tracker.reset()
                status = f"LINIE VERLOREN {lost_for:.1f}s"
                if flying and lost_for > args.lost_timeout and rescues < args.rescue_searches:
                    # Statt zu landen von oben suchen (wie beim Start neben der Linie). Flüge 16.11.:
                    # dreimal gelandet, obwohl die Linie nah war (nach einem Flip, zweimal an einer Kurve).
                    rescues += 1
                    logger.warning(f"Linie verloren – suche sie von oben ({rescues}/{args.rescue_searches}).")
                    tello.send_rc_control(0, 0, 0, 0)
                    rescue_start = time.monotonic()
                    search_line(tello, frame_reader, args, publish, logger=logger)  # FlightError -> Landung
                    after = time.monotonic()
                    controller.reset()
                    tracker.reset()
                    if height_hold is not None:
                        height_hold.pause(after)
                    if drone_guard is not None:
                        drone_guard.reset()
                    last_seen = after
                    if junctions is not None and junctions.asked_at is not None:
                        junctions.asked_at += after - rescue_start
                    last_tick = None
                    continue
                if flying and lost_for > args.lost_timeout:
                    logger.warning("Linie zu lange verloren – lande.")
                    break
            last_tick = now
            if line is not None or objects is None or not objects.cars.any():
                overfly_since = None

            if pad_watcher is not None:
                target_pad = pad_watcher.update(tello.get_mission_pad_id(), time.monotonic())
                if target_pad is not None:
                    status = f"PAD m{target_pad} – LANDEN"
                    if flying:
                        logger.info(f"Mission Pad m{target_pad} erkannt – lande darauf.")
                        state.status = status
                        frontend.show(None, state)
                        land_on_pad(tello, target_pad, min(100, max(30, tello.get_distance_tof())), logger=logger)
                        flying = False  # ist gelandet; der finally-Block landet nicht noch einmal
                        break
            if flying and junctions is not None and junctions.waiting and now - junctions.asked_at > args.junction_timeout:
                logger.warning("Keine Antwort an der Abzweigung – lande.")
                break
            if flying and cars is not None and cars.waiting and now - cars.since > args.car_timeout:
                logger.warning("Auto steht zu lange im Weg – lande.")
                break
            if args.dry_run and args.test_limit > 0 and now - loop_start > args.test_limit:
                logger.warning(f"Testmodus nach {args.test_limit:.0f} s beendet – der Videostream heizt die Drohne "
                               "am Boden um gut 2 °C pro Minute.")
                break
            if flying and tello.get_battery() < args.land_battery:
                logger.warning(f"Akku unter {args.land_battery} % – lande.")
                break
            if drone_guard is not None and objects is not None and objects.cars.any():
                drone_guard.reset()  # Auto unter/vor der Drohne: darüber hinwegfliegen, nicht ausweichen
            elif flying and drone_guard is not None and drone_guard.below(tello.get_distance_tof()):
                below_cm = tello.get_distance_tof()
                logger.warning(f"Etwas unter mir ({below_cm} cm statt {args.height} cm) – bleibe stehen und steige "
                               f"auf {drone_guard.guard_height} cm.")
                guard_start = time.monotonic()
                if not drone_guard.evade(tello, guard_tick, logger=logger):
                    logger.info("Landung angefordert.")
                    break
                after = time.monotonic()
                # Wie nach einem Flip: neu einfangen, die Wartezeit zählt nicht als Linienverlust
                # und nicht für die Zeitgrenzen an Abzweigung/Auto.
                controller.reset()
                tracker.reset()
                if height_hold is not None:
                    height_hold.pause(after)
                last_seen = after
                if junctions is not None and junctions.asked_at is not None:
                    junctions.asked_at += after - guard_start
                if cars is not None and cars.since is not None:
                    cars.since += after - guard_start
                continue
            if flying:
                ud = 0
                if height_hold is not None:
                    # Nicht regeln, wenn etwas Fremdes unter/vor der Drohne sein kann (siehe HeightHold).
                    blocked = (video_stalled or corrupt or (cars is not None and cars.waiting)
                               or (objects is not None and bool(objects.cars.any())))
                    was_correcting = height_hold.correcting
                    height = tello.get_distance_tof()
                    ud = height_hold.update(height, now, blocked)
                    if height_hold.correcting and not was_correcting:
                        logger.info(f"Höhe {height} cm statt {args.height} cm – korrigiere.")
                    elif was_correcting and not height_hold.correcting:
                        logger.info(f"Höhenkorrektur beendet bei {height} cm.")
                    if ud:
                        status += f" ud {ud:+d}"
                tello.send_rc_control(lr, fb, ud, yaw)

            waiting = junctions is not None and junctions.waiting
            view = draw_overlay(gray, mask, line, status, junctions.prompt() if waiting else "",
                                objects.cars if objects is not None else None)
            if recorder:
                recorder.write(frame.shape if frame is not None else None, gray, view, line, (lr, fb, yaw), status, tello,
                               corrupt, frame_reader is not None and frame_reader.decoder_corrupt)
            state.status, state.flying = status, flying
            state.prompt = tuple(junctions.options) if waiting else ()
            state.battery, state.height_cm, state.temp_c = tello.get_battery(), tello.get_distance_tof(), tello.get_temperature()
            state.speed_cm_s = int(round(10 * np.hypot(tello.get_speed_x(), tello.get_speed_y())))
            state.heading_deg = tello.get_yaw()
            state.offset, state.angle = (line.offset, line.angle) if line is not None else (None, None)
            state.command = (lr, fb, yaw)
            frontend.show(view, state)
            key = frontend.poll_key(1) or pending_key
            pending_key = None
            if key == "q":
                logger.info("Landung angefordert.")
                break
            if key in FLIP_KEYS:
                if not flying:
                    logger.info("Kein Flip im Testmodus – die Drohne ist am Boden.")
                elif (cars is not None and cars.waiting) or (objects is not None and bool(objects.cars.any())):
                    logger.warning("Kein Flip: Auto in der Nähe.")
                    if key.startswith("sync-"):
                        tello.send_rc_control(0, 0, 0, 0)
                        sync_wait(getattr(frontend, "sync_barrier", None), logger)  # die anderen nicht warten lassen
                        last_seen = time.monotonic()
                else:
                    state.status = f"FLIP {FLIP_NAMES[FLIP_KEYS[key]].upper()}"
                    frontend.show(None, state)
                    flip_start = time.monotonic()
                    direction = FLIP_KEYS[key]
                    barrier = getattr(frontend, "sync_barrier", None) if key.startswith("sync-") else None
                    flipped = do_flip(tello, direction, args.height, logger=logger, flip_height_cm=args.flip_height,
                                      barrier=barrier)
                    after = time.monotonic()
                    if flipped and direction in ("l", "r"):
                        # Flip nach links versetzt die Drohne nach links -> die Linie liegt rechts.
                        search_lr = SIDE_SEARCH_RC if direction == "l" else -SIDE_SEARCH_RC
                        search_until = after + SIDE_SEARCH_S
                        # Das erste Bild danach kann noch von vor dem Flip sein (Linie scheinbar noch da,
                        # in der Simulation beendete das die Suche sofort) – kurz nicht als Fund werten.
                        search_from = after + 0.3
                        logger.info(f"Suche die Linie {'rechts' if direction == 'l' else 'links'}.")
                    # Danach neu einfangen: Messwerte von vor dem Flip gelten nicht mehr, und die
                    # Sekunden des Flips zählen nicht als Linienverlust.
                    controller.reset()
                    tracker.reset()
                    if height_hold is not None:
                        height_hold.pause(after)
                    last_seen = after
                    # Wartezeiten an Abzweigung/Auto laufen während des Flips nicht weiter – Flug 13:38
                    # landete sonst direkt danach mit "Keine Antwort an der Abzweigung".
                    if junctions is not None and junctions.asked_at is not None:
                        junctions.asked_at += after - flip_start
                    if cars is not None and cars.since is not None:
                        cars.since += after - flip_start
                    # Während des Flips gedrückte Flip-Knöpfe verwerfen (Flug 13:38: zweiter Flip direkt
                    # hinterher, weil der Knopf noch einmal gedrückt war). Landen bleibt erhalten.
                    while (queued := frontend.poll_key(0)) is not None:
                        if queued not in FLIP_KEYS:
                            pending_key = pending_key or queued
                continue
            if waiting:
                if key == "l":
                    logger.info("Landung an der Abzweigung gewählt.")
                    break
                if args.random and key is None:
                    key = random.choice(list(junctions.options))  # --random: Richtung selbst auslosen
                bearing = junctions.choose(key)
                if bearing is not None:
                    logger.info(f"Abzweigung: {JunctionGuide.KEYS[key]} ({bearing:+.0f}°)")
                    tracker.target_bearing = bearing
                    tracker.keep_direction = True  # sofort, nicht erst beim nächsten Bild
    # Fehler hier protokollieren, solange events.log der Aufnahme noch offen ist (der
    # finally-Block schließt sie); vorher ging die Ursache nur auf die Konsole.
    except FlightError as error:
        logger.error(str(error))
        raise FlightError(str(error), logged=True) from error
    except TelloException as error:
        logger.error(f"Flug abgebrochen – Befehl an die Drohne gescheitert ({error}).")
        raise FlightError(f"Befehl an die Drohne gescheitert: {error}", logged=True) from error
    except Exception as error:
        logger.exception("Flug mit Fehler abgebrochen")
        raise FlightError(f"{type(error).__name__}: {error}", logged=True) from error
    finally:
        state.status, state.prompt = "LANDET" if flying else "BEENDET", ()
        frontend.show(None, state)
        # Jeder Aufräumschritt einzeln: Scheiterte einer (Flug 2.11. 11:13: "land" -> "error", die
        # Drohne war wegen leeren Akkus schon selbst gelandet), blieben sonst Videostream und
        # Kamera an (die Drohne heizte weiter, Kühlen half nicht mehr) und events.log offen.
        if flying:
            _cleanup(logger, "Schweben", tello.send_rc_control, 0, 0, 0, 0)
            if _cleanup(logger, "Landebefehl", tello.land, level=logging.ERROR,
                        hint="Drohne prüfen – ohne Befehle landet sie nach 15 s von selbst"):
                logger.log(SUCCESS, "Gelandet.")
        if frame_reader is not None:
            frame_reader.stop()
        if pad_watcher is not None:
            # sonst lehnt die Drohne danach den Kühlmodus ab (siehe cool_down)
            _cleanup(logger, "Mission-Pad-Erkennung ausschalten", tello.disable_mission_pads)
        _cleanup(logger, "Kamera auf vorne stellen", tello.set_video_direction, Tello.CAMERA_FORWARD)
        _cleanup(logger, "Videostream ausschalten", tello.streamoff)
        if args.video_bitrate != Tello.BITRATE_AUTO:
            _cleanup(logger, "Videobitrate zurücksetzen", tello.set_video_bitrate, Tello.BITRATE_AUTO)
        if video_port != Tello.VS_UDP_PORT:
            _cleanup(logger, "Videoport zurücksetzen", tello.change_vs_udp, Tello.VS_UDP_PORT,
                     hint="Drohne neu starten, bevor sie im Terminal fliegt")
        tello.end()
        if recorder:
            recorder.close()
        state.status, state.flying = "BEENDET", False
        frontend.show(None, state)
