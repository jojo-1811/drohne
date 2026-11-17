"""Prüfen, ob die Verbindung zu einer Drohne komplett durchgeht – auch aus einem anderen Netz.

Liegt der Rechner nicht im Drohnen-WLAN (z. B. im Client-Netz), laufen alle Pakete
über den Router. Befehle sind Antworten auf eine Anfrage und kommen meist durch.
Statuspakete (UDP 8890) und Video (UDP 11111) schickt die Drohne dagegen von sich
aus an den Rechner – eine Firewall zwischen den Netzen kann genau die blockieren.
Ohne sie kann der Linienfolger nicht fliegen.

    python net_check.py --drone 108

Die Drohne hebt nicht ab; nur die Kamera wird kurz eingeschaltet.
"""

from __future__ import annotations

import argparse
import socket
import time

from find_drones import TELLO_PORT, find_by_serial, serial_for

STATE_PORT = 8890
VIDEO_PORT = 11111


def wait_for_packets(port: int, source_ip: str, seconds: float) -> int:
    """Pakete zählen, die in seconds Sekunden von source_ip an diesen Port kommen."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", port))
        sock.settimeout(0.2)
        count = 0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                _, (ip, _) = sock.recvfrom(65535)
            except socket.timeout:
                continue
            count += ip == source_ip
        return count


def command(sock: socket.socket, ip: str, text: str) -> str:
    sock.sendto(text.encode(), (ip, TELLO_PORT))
    try:
        return sock.recvfrom(1024)[0].decode(errors="replace").strip()
    except socket.timeout:
        return "keine Antwort"


def main() -> None:
    p = argparse.ArgumentParser(description="Verbindung zur Drohne prüfen (Befehle, Status, Video).")
    p.add_argument("--drone", help="Name aus drones.json oder Seriennummer.")
    p.add_argument("--host", help="IP der Drohne (statt --drone).")
    args = p.parse_args()
    if not (args.drone or args.host):
        raise SystemExit("--drone oder --host angeben.")

    ip = args.host or find_by_serial(serial_for(args.drone))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_sock:
        probe_sock.connect((ip, TELLO_PORT))  # nur Routenwahl, es wird nichts gesendet
        own_ip = probe_sock.getsockname()[0]
    print(f"Drohne {ip}, dieser Rechner {own_ip}")

    results = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", 0))
        sock.settimeout(3)
        reply = command(sock, ip, "command")
        results["Befehle (UDP 8889, Antworten)"] = reply == "ok"
        print(f"  command -> {reply}")
        # Statuspakete schickt die Drohne nach "command" laufend mit ~10 Hz.
        state = wait_for_packets(STATE_PORT, ip, 2.0)
        results["Statuspakete (UDP 8890 an diesen Rechner)"] = state > 0
        print(f"  Statuspakete in 2 s: {state}")
        print(f"  streamon -> {command(sock, ip, 'streamon')}")
        video = wait_for_packets(VIDEO_PORT, ip, 4.0)
        print(f"  streamoff -> {command(sock, ip, 'streamoff')}")
        results["Video (UDP 11111 an diesen Rechner)"] = video > 0
        print(f"  Videopakete in 4 s: {video}")

    print()
    for name, ok in results.items():
        print(f"  {'OK    ' if ok else 'FEHLT '} {name}")
    if all(results.values()):
        print("\nAlles kommt an – der Linienfolger kann von hier aus fliegen.")
    elif results["Befehle (UDP 8889, Antworten)"]:
        print("\nBefehle gehen durch, aber Status und/oder Video kommen nicht an. Das liegt an der Firewall\n"
              f"zwischen den Netzen: Sie muss UDP von {ip} (bzw. dem Drohnennetz) an {own_ip}, Ports 8890 und\n"
              "11111–11118, durchlassen. Das muss der Netzwerk-Admin freigeben.")
    else:
        print("\nDie Drohne antwortet nicht – eingeschaltet, im WLAN und vom Router aus erreichbar?")


if __name__ == "__main__":
    main()
