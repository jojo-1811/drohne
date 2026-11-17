"""Webinterface: Drohnen im WLAN finden und den Linienfolger aus dem Browser starten.

Mehrere Drohnen können gleichzeitig fliegen; jede bekommt einen eigenen Videoport.
Im Browser: Live-Video, Status, Knöpfe für die Abzweigungen und zum Landen.

    python webapp.py                          # nur auf diesem Rechner: http://localhost:8000
    DRONE_WEB_PASSWORD=geheim python webapp.py --host 0.0.0.0   # auch für andere Geräte im WLAN

Von anderen Geräten aus ist ein Passwort Pflicht (Benutzername siehe --user). Das
Passwort wird über HTTP unverschlüsselt übertragen – nur im eigenen WLAN benutzen.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import queue
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from ipaddress import IPv4Network
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np
import uvicorn
from djitellopy import Tello
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from line_follower import parse_args
from src.line_follower.classes.flight_error import FlightError
from src.line_follower.classes.flight_state import FlightState
from src.line_follower.methods.cool import cool as run_cooling
from src.line_follower.methods.fly import fly as run_flight
from src.line_follower.utils import log
from find_drones import DRONES_FILE, drone_network, probe, query

WEB_DIR = Path(__file__).parent / "web"
# Die Seite (web/) liest der Server bei jedem Abruf frisch von der Platte, den Python-Code aber nur
# beim Start. Nach einem Update ohne Neustart passen Seite und Server nicht zusammen – die Seite
# erkennt das an dieser Nummer und sagt es, statt mit einem unverständlichen Fehler abzubrechen.
API_VERSION = 5
LOG_HISTORY = 500  # so viele Log-Einträge hält der Server für den Log-Reiter
SCAN_INTERVAL = 30.0  # Sekunden zwischen automatischen Suchen (hält auch die Akkuanzeige aktuell)
MAX_DRONES = 8  # Videoports 11111 … 11118
DEFAULT_ARGS = parse_args([])  # Standardwerte des Linienfolgers (Startoptionen im Browser)
KANON_DELAY_S = 5.0  # Abstand zwischen den Starts im Kanon
# Erste Drohne am tiefsten, die letzte am höchsten (cm). Nicht tiefer als 50: Auf 45 cm sieht die
# Kamera zu wenig Boden – die Drohne schwang am 16.11. (11:24) hinter einer Kurve über die Linie
# hinaus und verlor sie. Oben 90 statt 100 cm, damit die Linie dort breiter im Bild ist.
KANON_HEIGHTS = (50, 90)


class WebFrontend:
    """Verbindet einen Flug (run_flight, eigener Thread) mit dem Browser.

    Der Flug legt jedes Bild als JPEG ab; die Videoverbindungen der Browser warten
    darauf. Knöpfe im Browser landen als Tasten in einer Warteschlange, die der
    Flug wie die Tastatur des OpenCV-Fensters abfragt.
    """

    def __init__(self) -> None:
        self._keys: queue.Queue[str] = queue.Queue()
        self.sync_barrier: Optional[threading.Barrier] = None  # Synchron-Flip (siehe FleetManager.sync_flip)
        self._changed = threading.Condition()
        self.jpeg: Optional[bytes] = None
        self.frame_id = 0
        self.state = FlightState()

    def show(self, view: Optional[np.ndarray], state: FlightState) -> None:
        jpeg = None
        if view is not None:
            ok, encoded = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 75])
            jpeg = encoded.tobytes() if ok else None
        with self._changed:
            if jpeg is not None:
                self.jpeg, self.frame_id = jpeg, self.frame_id + 1
            self.state = dataclasses.replace(state)  # Kopie: der Flug ändert sein Objekt weiter
            self._changed.notify_all()

    def poll_key(self, wait_ms: int) -> Optional[str]:
        try:
            return self._keys.get(timeout=wait_ms / 1000)
        except queue.Empty:
            return None

    def press(self, key: str) -> None:
        self._keys.put(key)

    def next_frame(self, after: int, timeout: float) -> tuple[int, Optional[bytes]]:
        """Auf ein Bild warten, das neuer ist als after (höchstens timeout Sekunden)."""
        with self._changed:
            self._changed.wait_for(lambda: self.frame_id > after, timeout=timeout)
            return self.frame_id, self.jpeg


class LogBuffer(logging.Handler):
    """Alle Meldungen (Server und Flüge) für den Log-Reiter; jeder Eintrag mit fortlaufender ID,
    damit der Browser nur neue abholt."""

    def __init__(self) -> None:
        super().__init__()
        self._entries: deque[dict] = deque(maxlen=LOG_HISTORY)
        self._next_id = 1
        self._entries_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        drone = record.name.split(".", 1)[1] if "." in record.name else ""
        with self._entries_lock:
            self._entries.append({
                "id": self._next_id,
                "time": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "level": record.levelname, "drone": drone, "msg": record.getMessage(),
            })
            self._next_id += 1

    def since(self, after: int) -> list[dict]:
        with self._entries_lock:
            return [entry for entry in self._entries if entry["id"] > after]

    def last_for(self, drone: str) -> Optional[dict]:
        with self._entries_lock:
            return next((entry for entry in reversed(self._entries) if entry["drone"] == drone), None)

    def clear(self) -> None:
        with self._entries_lock:
            self._entries.clear()


@dataclasses.dataclass
class Flight:
    name: str
    serial: str
    host: str
    video_port: int  # 0 beim Kühlen (kein Video)
    frontend: WebFrontend
    kind: str = "flight"  # "flight" (Linienfolger) oder "cool" (Kühlmodus am Boden)
    error: Optional[str] = None
    started_at: float = dataclasses.field(default_factory=time.time)
    thread: threading.Thread = dataclasses.field(init=False)

    @property
    def running(self) -> bool:
        return self.thread.is_alive()


class StartRequest(BaseModel):
    drone: str  # Name aus drones.json oder Seriennummer
    dashed: bool = True
    record: bool = True
    dry_run: bool = False
    random: bool = False  # an Kreuzungen selbst eine zufällige Richtung wählen (--random)
    search_start: bool = False  # Start neben der Linie: von oben suchen (--search-start)
    drone_guard: bool = False  # über einer anderen Drohne aufsteigen und warten (--drone-guard)
    # Höchstens 30: Mit 50 verlor die Drohne am 2.11. (14:42) nach 8 s die Linie in der ersten
    # Kurve – bei ~0,9 s Reaktionszeit fliegt sie dabei weiter, als die Kamera nach vorn sieht.
    speed: int = Field(25, ge=10, le=30)
    # Seitenregler (siehe line_follower.parse_args); None = Standardwert
    k_lateral: Optional[float] = Field(None, ge=0, le=60)
    k_lateral_d: Optional[float] = Field(None, ge=0, le=40)
    k_lateral_i: Optional[float] = Field(None, ge=0, le=20)
    k_curve: Optional[float] = Field(None, ge=0, le=60)  # Kurven-Vorsteuerung
    video_bitrate: Optional[int] = Field(None, ge=0, le=5)  # Mbit/s, 0 = automatisch
    height: Optional[int] = Field(None, ge=40, le=100)  # Flughöhe in cm
    # Im Kanon je Drohne verschieden, damit sich die Schichten beim Suchen/Flippen nicht kreuzen
    search_height: Optional[int] = Field(None, ge=50, le=150)
    flip_height: Optional[int] = Field(None, ge=80, le=200)


TUNING_FIELDS = ("speed", "height", "k_lateral", "k_lateral_d", "k_lateral_i", "k_curve", "video_bitrate")


class CoolRequest(BaseModel):
    drone: str
    target: float = Field(70, ge=50, le=85)  # Zieltemperatur in °C


class KanonRequest(StartRequest):
    drone: str = ""  # nicht benutzt: der Kanon startet alle gefundenen, freien Drohnen


class KeyRequest(BaseModel):
    key: str = Field(pattern="^([awdlq]|(sync-)?flip-[fblr])$")  # flip-*: Flip-Knöpfe, sync-flip-*: alle zugleich


class ScanRequest(BaseModel):
    network: Optional[str] = None  # z. B. 10.0.11.0/24; leer = Standardnetz


def parse_network(text: str) -> IPv4Network:
    try:
        network = IPv4Network(text, strict=False)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Ungültiges Netz: {text}") from error
    if network.num_addresses > 4096:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Netz zu groß (höchstens /20).")
    return network


class FleetManager:
    """Drohnensuche und laufende Flüge; von mehreren Server-Threads gleichzeitig benutzt."""

    def __init__(self, network: Optional[IPv4Network] = None) -> None:
        self._fixed_network = network
        self.network: Optional[IPv4Network] = network
        self.log = LogBuffer()
        log.addHandler(self.log)  # Meldungen aller Flüge kommen über den Eltern-Logger an
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self.drones: dict[str, dict] = {}  # Seriennummer -> Name, IP, Akku
        self.scanned_at: Optional[float] = None
        self.flights: dict[str, Flight] = {}  # Seriennummer -> letzter Flug
        self.kanon: list[dict] = []  # letzter Kanon: Name, Seriennummer, Höhe, Startverzögerung
        self._kanon_stop = threading.Event()
        self._kanon_thread: Optional[threading.Thread] = None

    def scan(self, network: Optional[IPv4Network] = None) -> None:
        """Netz nach Drohnen durchsuchen (~3 s). Fliegende Drohnen werden nicht abgefragt.
        Ein im Browser angegebenes Netz gilt auch für die folgenden automatischen Suchen."""
        with self._scan_lock:
            if network is not None:
                self._fixed_network = network
            network = self._fixed_network or drone_network()
            names = {serial: name for name, serial in self._known_drones().items()}
            found: dict[str, dict] = {}
            for ip in probe(network, timeout=3.0):
                busy = next((f for f in self._running() if f.host == ip), None)
                if busy is not None:
                    found[busy.serial] = self.drones.get(busy.serial, {"serial": busy.serial, "name": busy.name, "ip": ip})
                    continue
                serial = query(ip, "sn?")
                if serial == "?":
                    continue
                battery = query(ip, "battery?")
                found[serial] = {"serial": serial, "name": names.get(serial, serial), "ip": ip,
                                 "battery": int(battery) if battery.isdigit() else None}
            with self._lock:
                changed = set(found) != set(self.drones) or network != self.network
                self.drones, self.scanned_at, self.network = found, time.time(), network
            if changed:  # die automatische Suche alle 30 s soll das Log nicht füllen
                names = ", ".join(f"{d['name']} ({d['ip']})" for d in found.values()) or "keine"
                (log.info if found else log.warning)(f"Suche in {network}: {names}")

    def _idle_drone(self, name: str) -> dict:
        """Gefundene Drohne, die gerade weder fliegt noch kühlt (Aufrufer hält self._lock)."""
        drone = next((d for d in self.drones.values() if name in (d["name"], d["serial"])), None)
        if drone is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Drohne {name} nicht gefunden – erst suchen.")
        previous = self.flights.get(drone["serial"])
        if previous is not None and previous.running:
            busy = "kühlt" if previous.kind == "cool" else "fliegt"
            raise HTTPException(status.HTTP_409_CONFLICT, f"Drohne {drone['name']} {busy} schon.")
        return drone

    def start(self, request: StartRequest) -> Flight:
        with self._lock:
            drone = self._idle_drone(request.drone)
            used = {f.video_port for f in self._running()}
            free = [port for port in range(Tello.VS_UDP_PORT, Tello.VS_UDP_PORT + MAX_DRONES) if port not in used]
            if not free:
                raise HTTPException(status.HTTP_409_CONFLICT, f"Höchstens {MAX_DRONES} Drohnen gleichzeitig.")

            args = parse_args([])
            args.dashed, args.record, args.dry_run, args.random = (request.dashed, request.record,
                                                                   request.dry_run, request.random)
            args.search_start, args.drone_guard = request.search_start, request.drone_guard
            if request.search_height is not None:
                args.search_height = request.search_height
            if request.flip_height is not None:
                args.flip_height = request.flip_height
            for name in TUNING_FIELDS:
                if getattr(request, name) is not None:
                    setattr(args, name, getattr(request, name))
            logger = log.getChild(drone["name"])  # Konsole und Log-Reiter über den Eltern-Logger
            frontend = WebFrontend()
            flight = Flight(drone["name"], drone["serial"], drone["ip"], free[0], frontend)
            flight.thread = threading.Thread(target=self._run, args=(flight, args, logger), daemon=True,
                                             name=f"flight-{drone['name']}")
            self.flights[drone["serial"]] = flight
        logger.info(f"Start: {request.model_dump()} (Videoport {flight.video_port})")
        flight.thread.start()
        return flight

    def cool(self, request: CoolRequest) -> Flight:
        """Kühlmodus am Boden; läuft wie ein Flug im eigenen Thread, "Landen" (q) bricht ab."""
        with self._lock:
            drone = self._idle_drone(request.drone)
            logger = log.getChild(drone["name"])
            flight = Flight(drone["name"], drone["serial"], drone["ip"], 0, WebFrontend(), kind="cool")
            flight.thread = threading.Thread(target=self._run_cooling, args=(flight, request.target, logger),
                                             daemon=True, name=f"cool-{drone['name']}")
            self.flights[drone["serial"]] = flight
        logger.info(f"Kühlen bis {request.target:.0f} °C angefordert.")
        flight.thread.start()
        return flight

    @staticmethod
    def kanon_heights(count: int, default: int) -> list[int]:
        """Flughöhen gleichmäßig zwischen KANON_HEIGHTS verteilt, auf 5 cm gerundet."""
        if count == 1:
            return [default]
        low, high = KANON_HEIGHTS
        return [int(5 * round((low + i * (high - low) / (count - 1)) / 5)) for i in range(count)]

    def start_kanon(self, request: KanonRequest) -> list[dict]:
        """Alle gefundenen, freien Drohnen nacheinander starten: alle KANON_DELAY_S Sekunden eine,
        jede auf eigener Höhe. Jede wählt an den Abzweigungen selbst zufällig: Denselben Weg wie die
        erste zu fliegen klappte am 16.11. nicht (Drohnen sehen je nach Startpunkt und Höhe nicht
        dieselben Abzweigungen); durch die verschiedenen Höhen sind auch Kreuzungen unkritisch."""
        with self._lock:
            if self._kanon_thread is not None and self._kanon_thread.is_alive():
                raise HTTPException(status.HTTP_409_CONFLICT, "Der Kanon startet gerade schon.")
            busy = {f.serial for f in self._running()}
            idle = sorted((d for d in self.drones.values() if d["serial"] not in busy), key=lambda d: d["name"])
            if not idle:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Keine freie Drohne gefunden – erst suchen.")
            idle = idle[:MAX_DRONES]
            heights = self.kanon_heights(len(idle), request.height or DEFAULT_ARGS.height)
            self.kanon = [{"name": d["name"], "serial": d["serial"], "height": h, "delay": i * KANON_DELAY_S,
                           "guard": i > 0, **self.kanon_layers(heights, i)} for i, (d, h) in enumerate(zip(idle, heights))]
            self._kanon_stop.clear()
            self._kanon_thread = threading.Thread(target=self._run_kanon, args=(request, list(self.kanon)),
                                                  daemon=True, name="kanon")
        names = ", ".join(f"{m['name']} {m['height']} cm (+{m['delay']:.0f} s, Suche {m['search_height']}, "
                          f"Flip {m['flip_height']} cm{', weicht aus' if m['guard'] else ''})"
                          for m in self.kanon)
        log.info(f"Kanon: {names}")
        self._kanon_thread.start()
        return self.kanon

    @staticmethod
    def kanon_layers(heights: list[int], i: int) -> dict:
        """Such- und Fliphöhe der i-ten Drohne im Kanon: Sie sollen nicht in die Schicht der nächst-
        höheren Drohne geraten. Suchen: unter der nächsthöheren (mind. 20 cm Abstand), die oberste auf
        100 cm bzw. 10 cm über ihrer Flughöhe. Flip: 100 cm, jede höhere 40 cm darüber."""
        top = i == len(heights) - 1
        search = max(DEFAULT_ARGS.search_height, heights[i] + 10) if top else max(50, heights[i + 1] - 20)
        return {"search_height": min(150, search), "flip_height": min(200, 100 + 40 * i)}

    def _run_kanon(self, request: KanonRequest, members: list[dict]) -> None:
        start = time.monotonic()
        for i, member in enumerate(members):
            if self._kanon_stop.wait(max(0.0, start + member["delay"] - time.monotonic())):
                log.warning("Kanon abgebrochen – weitere Drohnen starten nicht.")
                return
            # Alle außer der untersten weichen nach oben aus, wenn eine Drohne unter ihnen ist.
            single = StartRequest(**{**request.model_dump(), "drone": member["serial"], "height": member["height"],
                                     "random": True, "drone_guard": i > 0, "search_height": member["search_height"],
                                     "flip_height": member["flip_height"]})
            try:
                self.start(single)
            except HTTPException as error:
                log.warning(f"Kanon: {member['name']} startet nicht ({error.detail}).")

    def kanon_press(self, key: str) -> int:
        """Knopf im Kanon-Modus: an alle laufenden Flüge, für die er gerade passt. Gibt die Anzahl zurück."""
        if key.startswith("sync-flip-"):
            return len(self.sync_flip(key))
        targets = []
        for flight in self._running():
            state = flight.frontend.state
            if key == "q" or (key.startswith("flip-") and state.flying and flight.kind == "flight") \
                    or (key in "awdl" and state.prompt and (key == "l" or key in state.prompt)):
                targets.append(flight)
        if key == "q":
            self._kanon_stop.set()  # noch nicht gestartete Drohnen bleiben am Boden
        for flight in targets:
            flight.frontend.press(key)
        return len(targets)

    def sync_flip(self, key: str) -> list[str]:
        """Synchron-Flip: alle fliegenden Drohnen steigen auf ihre Fliphöhe, warten aufeinander
        (threading.Barrier) und flippen gleichzeitig. Gibt die Namen der beteiligten Drohnen zurück."""
        # Nur Drohnen, die gerade Tasten annehmen: Beim Suchen oder Ausweichen verwerfen sie Flip-Tasten,
        # die anderen warteten dann bis zum Zeitlimit.
        ready = ("FOLGEN", "ABZWEIGUNG", "ÜBER AUTO", "AUTO", "LINIE VERLOREN", "ZURÜCK")
        targets = [f for f in self._running() if f.kind == "flight" and f.frontend.state.flying
                   and f.frontend.state.status.startswith(ready)]
        if not targets:
            raise HTTPException(status.HTTP_409_CONFLICT, "Synchron-Flip nur im Flug – keine Drohne fliegt.")
        barrier = threading.Barrier(len(targets))
        for flight in targets:
            flight.frontend.sync_barrier = barrier
        for flight in targets:
            flight.frontend.press(key)
        names = [f.name for f in targets]
        log.info(f"Synchron-Flip {key.removeprefix('sync-flip-')}: {', '.join(names)}")
        return names

    def press(self, name: str, key: str) -> None:
        flight = self.find(name)
        if flight is None or not flight.running:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Kein laufender Flug für {name}.")
        flight.frontend.press(key)

    def land_all(self) -> None:
        self._kanon_stop.set()  # Not-Aus: auch keine weiteren Kanon-Starts
        for flight in self._running():
            flight.frontend.press("q")

    def stop_all(self, timeout: float = 20.0) -> None:
        """Beim Beenden des Servers: alle landen lassen und darauf warten."""
        self.land_all()
        deadline = time.monotonic() + timeout
        for flight in list(self.flights.values()):
            flight.thread.join(max(0.0, deadline - time.monotonic()))

    def scan_periodically(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.scan()
            except OSError as error:  # z. B. Netzwechsel des Rechners – beim nächsten Mal erneut
                log.warning(f"Drohnensuche fehlgeschlagen: {error}")
            stop.wait(SCAN_INTERVAL)

    def snapshot(self, log_after: Optional[int] = None) -> dict:
        """Zustand für den Browser; mit log_after zusätzlich alle neueren Log-Einträge."""
        with self._lock:
            drones = {serial: dict(drone) for serial, drone in self.drones.items()}
            for flight in self.flights.values():
                # Während eines Flugs ist der Akkuwert aus den Statuspaketen aktueller als der der Suche.
                if flight.running and flight.serial in drones and flight.frontend.state.battery is not None:
                    drones[flight.serial]["battery"] = flight.frontend.state.battery
            flights = [{
                "name": f.name, "serial": f.serial, "host": f.host, "running": f.running, "error": f.error,
                "started_at": f.started_at, "video_port": f.video_port, "kind": f.kind,
                **dataclasses.asdict(f.frontend.state), "last_event": self.log.last_for(f.name),
            } for f in self.flights.values()]
            return {"api_version": API_VERSION, "drones": sorted(drones.values(), key=lambda d: d["name"]),
                    "scanned_at": self.scanned_at, "network": str(self.network) if self.network else None,
                    "defaults": {name: getattr(DEFAULT_ARGS, name) for name in TUNING_FIELDS},
                    "flights": flights, "log": self.log.since(log_after) if log_after is not None else [],
                    "kanon": {"members": self.kanon, "delay": KANON_DELAY_S, "heights": KANON_HEIGHTS,
                              "starting": self._kanon_thread is not None and self._kanon_thread.is_alive()}}

    def find(self, name: str) -> Optional[Flight]:
        with self._lock:
            return next((f for f in self.flights.values() if name in (f.name, f.serial)), None)

    @staticmethod
    def _run_cooling(flight: Flight, target: float, logger: logging.Logger) -> None:
        try:
            run_cooling(flight.host, target, flight.frontend, logger=logger)
        except FlightError as error:
            flight.error = str(error)
            if not error.logged:
                logger.error(str(error))
        except Exception as error:  # Thread darf nicht still sterben – Fehler im Browser zeigen
            flight.error = f"{type(error).__name__}: {error}"
            logger.exception("Kühlen mit Fehler abgebrochen")

    def _running(self) -> list[Flight]:
        return [f for f in list(self.flights.values()) if f.running]

    @staticmethod
    def _known_drones() -> dict[str, str]:
        return json.loads(DRONES_FILE.read_text()) if DRONES_FILE.exists() else {}

    @staticmethod
    def _run(flight: Flight, args: argparse.Namespace, logger: logging.Logger) -> None:
        try:
            run_flight(args, flight.host, flight.frontend, logger=logger, video_port=flight.video_port, name=flight.name)
        except FlightError as error:
            flight.error = str(error)
            if not error.logged:
                logger.error(f"Nicht gestartet: {error}")
        except Exception as error:  # Thread darf nicht still sterben – Fehler im Browser zeigen
            flight.error = f"{type(error).__name__}: {error}"
            logger.exception("Flug mit Fehler abgebrochen")


def create_app(manager: FleetManager, user: str, password: Optional[str]) -> FastAPI:
    security = HTTPBasic(auto_error=password is not None)

    def check_password(credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> None:
        if password is None:
            return
        user_ok = secrets.compare_digest(credentials.username.encode(), user.encode())
        password_ok = secrets.compare_digest(credentials.password.encode(), password.encode())
        if not (user_ok and password_ok):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falsches Passwort",
                                headers={"WWW-Authenticate": "Basic"})

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = threading.Event()
        threading.Thread(target=manager.scan_periodically, args=(stop,), daemon=True, name="scan").start()
        yield
        stop.set()
        manager.stop_all()

    app = FastAPI(title="Smartcity-Drohnen", lifespan=lifespan, dependencies=[Depends(check_password)])

    # Seite immer beim Server nachfragen lassen: Sonst nimmt der Browser nach einem Update das alte
    # app.js aus dem Cache und meldet "alte Version", obwohl der Server neu ist (16.11.2026).
    no_cache = {"Cache-Control": "no-cache"}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", headers=no_cache)

    @app.get("/app.js")
    def script() -> FileResponse:
        return FileResponse(WEB_DIR / "app.js", media_type="text/javascript", headers=no_cache)

    @app.get("/style.css")
    def style() -> FileResponse:
        return FileResponse(WEB_DIR / "style.css", media_type="text/css", headers=no_cache)

    @app.get("/api/state")
    def state(since: int = 0) -> dict:
        return manager.snapshot(log_after=since)

    @app.post("/api/scan")
    def scan(request: ScanRequest) -> dict:
        manager.scan(parse_network(request.network) if request.network else None)
        return manager.snapshot()  # ohne Log – das holt die laufende Abfrage

    @app.post("/api/log/clear")
    def clear_log() -> dict:
        manager.log.clear()
        return {"ok": True}

    @app.post("/api/flights", status_code=status.HTTP_201_CREATED)
    def start(request: StartRequest) -> dict:
        flight = manager.start(request)
        return {"name": flight.name}

    @app.post("/api/cool", status_code=status.HTTP_201_CREATED)
    def cool(request: CoolRequest) -> dict:
        flight = manager.cool(request)
        return {"name": flight.name}

    @app.post("/api/flights/{name}/key")
    def key(name: str, request: KeyRequest) -> dict:
        manager.press(name, request.key)
        return {"ok": True}

    @app.post("/api/kanon", status_code=status.HTTP_201_CREATED)
    def kanon(request: KanonRequest) -> dict:
        return {"members": manager.start_kanon(request)}

    @app.post("/api/kanon/key")
    def kanon_key(request: KeyRequest) -> dict:
        return {"count": manager.kanon_press(request.key)}

    @app.post("/api/sync-flip")
    def sync_flip(request: KeyRequest) -> dict:
        key = request.key if request.key.startswith("sync-") else f"sync-{request.key}"
        if not key.startswith("sync-flip-"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nur Flip-Richtungen.")
        return {"drones": manager.sync_flip(key)}

    @app.post("/api/land-all")
    def land_all() -> dict:
        manager.land_all()
        return {"ok": True}

    @app.get("/api/flights/{name}/video")
    def video(name: str) -> StreamingResponse:
        flight = manager.find(name)
        if flight is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Kein Flug für {name}.")
        return StreamingResponse(_mjpeg(flight), media_type="multipart/x-mixed-replace; boundary=frame")

    return app


def _mjpeg(flight: Flight) -> Iterator[bytes]:
    """Bilder des Flugs als MJPEG-Strom (vom Browser direkt in <img> angezeigt)."""
    last = 0
    while True:
        last, jpeg = flight.frontend.next_frame(last, timeout=1.0)
        if jpeg is not None:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        if not flight.running and flight.frontend.frame_id <= last:
            return


def main() -> None:
    p = argparse.ArgumentParser(description="Webinterface für den Linienfolger.")
    p.add_argument("--host", default="127.0.0.1",
                   help="Adresse des Webservers; 0.0.0.0 = auch für andere Geräte im WLAN (dann Passwort nötig).")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--network", help="Netz der Drohnen (Standard: 10.0.11.0/24 bzw. DRONE_NETWORK).")
    p.add_argument("--user", default="drohne", help="Benutzername für die Anmeldung.")
    args = p.parse_args()

    password = os.environ.get("DRONE_WEB_PASSWORD") or None
    if args.host not in ("127.0.0.1", "localhost") and password is None:
        raise SystemExit("Für Zugriff aus dem WLAN ein Passwort setzen: DRONE_WEB_PASSWORD=... python webapp.py --host 0.0.0.0")

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    log.addHandler(console)
    log.setLevel(logging.INFO)
    log.propagate = False

    network = IPv4Network(args.network) if args.network else None
    print(f"Drohnennetz {network or drone_network()} – "
          f"Webinterface auf http://{args.host}:{args.port}")
    uvicorn.run(create_app(FleetManager(network), args.user, password), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
