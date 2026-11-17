"""Tello-/RoboMaster-TT-Drohnen in einem Netz finden.

Schickt jedem Host im angegebenen Netz das SDK-Kommando "command" per UDP an
Port 8889. Nur Tellos antworten darauf mit "ok"; danach wird die Seriennummer
und der Akkustand abgefragt.

    python find_drones.py                 # Drohnennetz durchsuchen (Standard 10.0.11.0/24)
    python find_drones.py 10.0.50.0/24    # bestimmtes Netz

Der Rechner muss nicht selbst im Drohnennetz sein: Aus einem anderen Netz (z. B. dem
Client-Netz) geht es über den Router, solange der die Pakete durchlässt (siehe net_check.py).
Anderes Standardnetz: Umgebungsvariable DRONE_NETWORK.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import time
from pathlib import Path
from typing import Optional

TELLO_PORT = 8889
DRONES_FILE = Path(__file__).parent / "drones.json"
# WLAN SmartCity-Drohnen. Früher wurde das eigene /24-Netz des Rechners durchsucht – das
# klappt nur, wenn er selbst im Drohnen-WLAN ist, nicht aus dem Client-Netz.
DEFAULT_NETWORK = "10.0.11.0/24"
PROBE_ROUNDS = 2
PROBE_ROUND_GAP = 1.0  # Sekunden zwischen den Suchrunden


def drone_network() -> ipaddress.IPv4Network:
    """Netz, in dem die Drohnen hängen (DRONE_NETWORK oder DEFAULT_NETWORK)."""
    return ipaddress.ip_network(os.environ.get("DRONE_NETWORK", DEFAULT_NETWORK), strict=False)


def serial_for(name: str) -> str:
    """Kurzname aus drones.json (z. B. "108") in die Seriennummer übersetzen; sonst ist name schon die SN."""
    if DRONES_FILE.exists():
        return json.loads(DRONES_FILE.read_text()).get(name, name)
    return name


def find_by_serial(serial: str, network: Optional[ipaddress.IPv4Network] = None, timeout: float = 3.0) -> str:
    """IP der Drohne mit dieser Seriennummer finden – die IP vergibt der Router (DHCP) und kann sich ändern."""
    network = network or drone_network()
    for ip in probe(network, timeout):
        try:
            if query(ip, "sn?") == serial:
                return ip
        except OSError:  # Host zwischendurch nicht erreichbar (PR #1) – nächsten prüfen
            continue
    raise LookupError(f"Drohne {serial} nicht in {network} gefunden – eingeschaltet und im WLAN?")


def probe(network: ipaddress.IPv4Network, timeout: float) -> list[str]:
    """IPs aller Drohnen im Netz, die auf "command" mit "ok" antworten.

    Zwei Runden: Liegt das Netz hinter einem Router, verwirft der das erste Paket an
    einen Host, dessen MAC-Adresse er noch nicht kennt (ARP-Auflösung läuft). Aus dem
    Client-Netz fand eine einzelne Runde die Drohne deshalb nicht, obwohl sie auf
    direkte Befehle antwortete (26.10.). Die zweite Runde geht nur an Hosts, die noch
    nicht geantwortet haben.
    """
    found: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", 0))
        sock.settimeout(0.2)

        def collect(until: float) -> None:
            while time.monotonic() < until:
                try:
                    data, (ip, _) = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                if data.strip().lower() == b"ok" and ip not in found:
                    found.append(ip)

        for round_ in range(PROBE_ROUNDS):
            for host in network.hosts():
                if str(host) in found:
                    continue
                try:
                    sock.sendto(b"command", (str(host), TELLO_PORT))
                except OSError:
                    # Im eigenen Netz meldet das System nach erfolgloser ARP-Anfrage aus der
                    # ersten Runde "No route to host" – dort ist sicher kein Gerät.
                    continue
                time.sleep(0.002)  # Netz nicht fluten
            if round_ < PROBE_ROUNDS - 1:
                collect(time.monotonic() + PROBE_ROUND_GAP)
        collect(time.monotonic() + timeout)
    return found


def query(ip: str, cmd: str, timeout: float = 2.0) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(cmd.encode(), (ip, TELLO_PORT))
        try:
            return sock.recvfrom(1024)[0].decode(errors="replace").strip()
        except socket.timeout:
            return "?"


def main() -> None:
    p = argparse.ArgumentParser(description="Tello-Drohnen im Netz finden.")
    p.add_argument("network", nargs="?", help=f"Netz in CIDR-Schreibweise (Standard: {DEFAULT_NETWORK})")
    p.add_argument("--timeout", type=float, default=3.0, help="Sekunden auf Antworten warten.")
    args = p.parse_args()

    network = ipaddress.ip_network(args.network, strict=False) if args.network else drone_network()
    if network.num_addresses > 4096:
        raise SystemExit("Netz zu groß (max. /20) – bitte genauer angeben.")

    print(f"Suche in {network} ({network.num_addresses - 2} Hosts) ...")
    found = probe(network, args.timeout)
    if not found:
        print("Keine Drohne gefunden.")
        return
    names = {sn: name for name, sn in json.loads(DRONES_FILE.read_text()).items()} if DRONES_FILE.exists() else {}
    for ip in sorted(found, key=ipaddress.ip_address):
        sn = query(ip, "sn?")
        name = f"[{names[sn]}]" if sn in names else ""
        print(f"{ip:15s}  SN {sn:20s} {name:6s} Akku {query(ip, 'battery?')} %")


if __name__ == "__main__":
    main()
