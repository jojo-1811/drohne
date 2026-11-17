"""RoboMaster TT (Tello Talent) in ein WLAN einbinden (Router-/STA-Modus).

Ablauf:
    1. Drohne einschalten, Schalter am Erweiterungsmodul auf AP (Direktverbindung).
    2. Mac mit dem WLAN der Drohne verbinden (RMTT-xxxxxx).
    3. python setup_wifi.py
    4. Drohne ausschalten, Schalter auf STA, wieder einschalten.
       Sie verbindet sich jetzt selbst mit dem angegebenen WLAN.
    5. Mac wieder ins Ziel-WLAN, dann: python find_drones.py <netz>/24
"""

from __future__ import annotations

import argparse
import getpass
import socket

DRONE_IP = "192.168.10.1"
TELLO_PORT = 8889


def send(sock: socket.socket, ip: str, cmd: str) -> str:
    sock.sendto(cmd.encode(), (ip, TELLO_PORT))
    try:
        return sock.recvfrom(1024)[0].decode(errors="replace").strip()
    except socket.timeout:
        return "keine Antwort"


def main() -> None:
    p = argparse.ArgumentParser(description="Tello Talent in ein WLAN einbinden.")
    p.add_argument("--ssid", default="SmartCity-Drohnen", help="Name des Ziel-WLANs.")
    p.add_argument("--host", default=DRONE_IP, help="IP der Drohne im eigenen WLAN.")
    args = p.parse_args()

    if " " in args.ssid:
        raise SystemExit("WLAN-Namen mit Leerzeichen kann das Tello-SDK nicht übertragen.")
    password = getpass.getpass(f"Passwort für '{args.ssid}': ")
    if " " in password:
        raise SystemExit("Passwörter mit Leerzeichen kann das Tello-SDK nicht übertragen.")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        if send(sock, args.host, "command") != "ok":
            raise SystemExit(f"Drohne unter {args.host} antwortet nicht – mit dem RMTT-WLAN verbunden?")
        print(f"Drohne gefunden, SN {send(sock, args.host, 'sn?')}")

        reply = send(sock, args.host, f"ap {args.ssid} {password}")
        print(f"Antwort der Drohne: {reply}")

    print(
        "\nNächste Schritte:\n"
        "  1. Drohne ausschalten\n"
        "  2. Schalter am Erweiterungsmodul auf STA stellen\n"
        "  3. Drohne einschalten, ca. 30 s warten\n"
        f"  4. Mac wieder mit '{args.ssid}' verbinden und Drohne suchen:\n"
        "       python find_drones.py 10.0.11.0/24"
    )


if __name__ == "__main__":
    main()
