# Tello Line Follower

Linienverfolgung für die **RoboMaster TT (Tello Talent)** mit Python, OpenCV und [djitellopy](https://github.com/damiafuentes/DJITelloPy).
Die Drohne nutzt ihre nach unten gerichtete Kamera, erkennt eine Linie am Boden und regelt Seitenversatz und Drehung.

**Schnelleinstieg:** [ANLEITUNG.md](ANLEITUNG.md) – die wichtigsten Befehle, Tasten und Lösungen für häufige Probleme.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Verbindung zur Drohne

**Variante A – direkt:** Schalter am Erweiterungsmodul auf **AP**, Mac mit dem WLAN `RMTT-xxxxxx` verbinden. Die Drohne hat dann immer die IP `192.168.10.1` (Standard, kein `--host` nötig).

**Variante B – über das WLAN `SmartCity-Drohnen`:**

1. Einmalig pro Drohne: Schalter auf **AP**, Mac mit `RMTT-xxxxxx` verbinden, dann
   ```bash
   python setup_wifi.py            # fragt nach dem WLAN-Passwort
   ```
2. Drohne aus, Schalter auf **STA**, Drohne an, ca. 30 s warten.
3. Mac mit `SmartCity-Drohnen` verbinden und Drohne suchen:
   ```bash
   python find_drones.py 10.0.11.0/24
   ```
   Ausgabe z. B. `10.0.11.108  SN 0TQZ...  [108]  Akku 92 %`.
   Der Mac muss dafür nicht im Drohnen-WLAN sein: Aus einem anderen Netz (z. B. Client-Netz) geht es über den
   Router – sofern die Firewall Status (UDP 8890) und Video (UDP 11111–11118) von den Drohnen zum Mac durchlässt.
   Prüfen mit `python net_check.py --drone 108`. Standardnetz der Suche ist 10.0.11.0/24 (änderbar über die
   Umgebungsvariable `DRONE_NETWORK`).
4. Die IP vergibt der Router und kann sich ändern. Deshalb die Drohne am besten über ihren Namen ansprechen:
   `python line_follower.py --drone 108 ...` – Namen und Seriennummern stehen in `drones.json`
   (neue Drohne: Seriennummer aus `find_drones.py` dort eintragen).

## Benutzung

```bash
# 1. Testmodus – Drohne bleibt am Boden, nur Erkennung anzeigen
python line_follower.py --drone 108 --dry-run

# 2. Fliegen (mit Aufzeichnung)
python line_follower.py --drone 108 --record
```

Die Kameradrehung für die RMTT (`--rotate 90`) ist fest eingestellt. Nur bei einem anderen Modell oder
falls das Bild verdreht aussieht: `python calibrate.py --drone 108` bzw. Papier-Pfeil-Test im Testmodus.

Im Videofenster: **`q`** = landen und beenden, **`f`** = Flip rückwärts.

**Flip:** Im Flug per Taste `f` (Videofenster) oder im Webinterface über die vier Flip-Knöpfe (vorwärts, rückwärts,
links, rechts; Taste F = rückwärts). Die Drohne hält an, steigt auf 1 m (ein Flip kostet 20–30 cm Höhe), flippt und
sinkt zurück auf die Flughöhe; danach folgt sie wieder der Linie. Erst ab 60 % Akku (die Tello lehnt Flips unter ~50 % ab, gemessen unter Last – daher Reserve),
nicht im Testmodus und nicht, solange ein Auto in der Nähe ist. Vorwärts und rückwärts
bleibt sie über der Linie; nach einem Flip nach links oder rechts schiebt sie sich von selbst in die Gegenrichtung
zurück, bis die Linie wieder im Bild ist (höchstens 5 s, sonst Landung), und folgt ihr dann weiter.
Decke und Umgebung müssen mindestens 1,5 m Platz bieten.

**Abzweigungen:** Sobald eine Kreuzung oder ein T-Stück in Sicht kommt, fliegt die Drohne langsamer, bleibt dann
davor in der Luft stehen und fragt im Videofenster (und im Terminal), wohin sie soll. Beim Warten hält sie die
Kreuzung knapp vor sich im Bild – gleitet sie zu weit, setzt sie zurück. Angeboten werden nur die Richtungen, die
es dort gibt:

| Taste | Richtung |
|---|---|
| `a` | links |
| `w` | geradeaus |
| `d` | rechts |
| `l` | hier landen |

Ohne Antwort landet sie nach 30 s (`--junction-timeout`). Mit `--junction auto` fliegt sie ohne Nachfrage
(Kreuzung: geradeaus, T-Stück: der Ast, der am wenigsten von geradeaus abweicht).
Sicherheit: Landung nach 2 s ohne Linie, kein Start unter 25 % Akku, Landung auch bei Fehlern.

**Autos auf der Strecke:** Steht ein Auto (Roboter) vorne auf der Linie, bleibt die Drohne in der Luft stehen
(Anzeige „AUTO – WARTE“, das Auto ist violett umrandet) und fliegt von selbst weiter, sobald es 1 s lang weg ist.
Ist es zu nah, setzt sie zurück; verdeckt es die Linie, schwebt sie, statt die Linie als verloren zu werten. Steht
es nach 30 s (`--car-timeout`) noch da, landet sie. Autos neben der Strecke stören nicht. Mission Pads werden an
ihren runden Punkten erkannt und nicht für Autos gehalten. Ausschalten mit `--no-cars`.

**Bildstörungen:** Zerstörte Bilder (Klötzchen-Artefakte) werden erkannt und nicht zum Steuern benutzt – die
Drohne schwebt dann auf der Stelle. Ist in einer Sekunde mindestens die Hälfte der Bilder zerstört oder kommt
gar kein Bild mehr, wird die Kamera neu gestartet (höchstens 3× pro Flug). Ohne brauchbares Bild landet sie
nach 8 s.

## Webinterface

Drohnen im Browser finden, den Linienfolger starten, Live-Video und Status sehen, an Abzweigungen per Knopf
wählen und landen – auch mit mehreren Drohnen gleichzeitig.

```bash
python webapp.py                                              # nur dieser Rechner: http://localhost:8000
DRONE_WEB_PASSWORD=geheim python webapp.py --host 0.0.0.0     # auch Tablet/Handy im WLAN: http://<IP des Macs>:8000
```

- Von anderen Geräten aus ist ein Passwort Pflicht (Benutzer `drohne`, änderbar mit `--user`). Es geht über
  HTTP unverschlüsselt – nur im eigenen WLAN benutzen.
- Optionen für den Start (gestrichelte Linie, Aufnahme, Testmodus, Geschwindigkeit) stehen über der Drohnenliste.
- **Alle landen** schickt jeder fliegenden Drohne den Landebefehl. Wird der Server beendet (Strg+C), landen
  alle Drohnen, bevor er sich schließt.
- Mehrere Drohnen: Jede bekommt einen eigenen Videoport (11111, 11112, …) über den SDK-Befehl `port`. Die
  erste Drohne nutzt den Standardport, für sie ändert sich nichts. Mehrere gleichzeitig ist noch nicht mit
  echten Drohnen getestet.
- Aufnahmen landen wie gewohnt in `recordings/`, mit dem Drohnennamen im Ordnernamen.

## Aufbau des Codes

| Datei / Ordner | Inhalt |
|---|---|
| `line_follower.py` | Kommandozeile: Optionen (`parse_args`) und Start eines Flugs mit OpenCV-Fenster |
| `src/line_follower/methods/fly.py` | Der Flug selbst (Erkennung, Regelung, Abzweigungen, Autos, Landung) – von Terminal und Webinterface genutzt |
| `src/line_follower/methods/` | übrige Funktionen, eine Datei pro Funktion (Erkennung, Video, Pad-Landung, Kühlen …) |
| `src/line_follower/classes/` | Klassen (Regler, Abzweigungen, Aufzeichnung, Anzeige-Schnittstelle `Frontend` …) |
| `src/line_follower/utils.py` | gemeinsame Konstanten und Hilfsfunktionen, Logger |
| `webapp.py`, `web/` | Webinterface (Server und Seite) |
| `find_drones.py`, `net_check.py`, `cool.py`, `step_test.py`, `calibrate.py`, `setup_wifi.py` | Hilfsprogramme |

## Wichtige Optionen

| Option | Bedeutung |
|---|---|
| `--host IP` | IP der Drohne (Standard `192.168.10.1`) |
| `--drone NAME` | Drohne über Namen aus `drones.json` (z. B. `108`) oder Seriennummer – die aktuelle IP wird automatisch gesucht |
| `--rotate 0/90/180/270` | Bild drehen, sodass „oben" = Flugrichtung (Standard 90 = RMTT) |
| `--flip` | Bild spiegeln (wenn `calibrate.py` das meldet) |
| `--record` | Flug aufzeichnen: `recordings/<Zeit>/video.mp4` + `log.csv` (Messwerte, Steuerbefehle, Höhe, Akku) + `events.log` (Ereignisse) |
| `--max-line-width` | Dunkle Flächen breiter als dieser Bildanteil (Mission Pad, Roboter) werden ignoriert (Standard 0.2) |
| `--corrupt-threshold` | Ab dieser Kantenstärke gilt ein Bild als zerstört (Standard 50; normale Bilder 15–30, Nahaufnahme eines Roboters bis ~40, zerstörte meist 60–85) |
| `--video-restarts` | Höchstzahl Kamera-Neustarts pro Flug (Standard 3) |
| `--video-timeout` | Sekunden ohne brauchbares Bild, bevor gelandet wird (Standard 8) |
| `--junction ask/auto` | An Abzweigungen anhalten und fragen (Standard) oder selbst entscheiden |
| `--junction-timeout` | Sekunden Wartezeit auf die Antwort an einer Abzweigung, danach Landung (Standard 30) |
| `--random` | An Abzweigungen selbst eine zufällige Richtung wählen statt zu fragen (Webinterface: „Zufällige Richtung“) |
| `--test-limit` | Testmodus nach so vielen Sekunden beenden (Standard 180; der Videostream heizt am Boden) |
| `--cars` / `--no-cars` | Vor Autos auf der Strecke anhalten, bis sie weg sind (Standard an) |
| `--car-timeout` | Sekunden Wartezeit vor einem Auto, danach Landung (Standard 30) |
| `--pad-landing` / `--no-pad-landing` | Auf dem Mission Pad am Streckenende landen (Standard an; das Start-Pad zählt nicht) |
| `--max-temp` | Nicht starten ab dieser Temperatur (Standard 80 °C) |
| `--cool` | Ist die Drohne zu heiß, vorher im Kühlmodus abkühlen (Propeller drehen, kein Abheben) |
| `--dashed` | Gestrichelte Linie: Striche zu einer Linie verbinden, auch an Ecken mit versetzten Strichen (`--max-gap` = größte Lücke als Anteil der Bildbreite, Standard 0.25) |
| `--light-line` | Helle Linie auf dunklem Boden |
| `--threshold N` | Fester Schwellwert 0–255 statt adaptiv |
| `--contrast N` | Adaptiv: so viel dunkler als die Umgebung muss die Linie sein (Standard 25) |
| `--speed N` | Vorwärtsgeschwindigkeit (RC 0–100) |
| `--k-lateral`, `--k-lateral-d`, `--k-yaw`, `--k-yaw-d` | Reglerverstärkungen (P/D); seitlich aus dem Sprungtest berechnet (`step_test.py`, Standard 20 / 9) |
| `--min-rc`, `--min-rc-lateral` | Totzonen-Ausgleich für Drehung/Vor-Zurück (Standard 15) bzw. seitwärts (Standard 0 – die Drohne reagiert seitwärts schon auf 10) |
| `--k-lateral-i` | I-Anteil seitlich gegen gleichmäßige Seitendrift im Vorwärtsflug (Standard 5, 0 = aus) |
| `--k-curve` | Kurven-Vorsteuerung: lenkt je nach Kurvenwinkel und Tempo vorab seitlich nach innen, damit die Drohne nicht nach außen getragen wird (Standard 12, 0 = aus) |
| `--height-hold` / `--no-height-hold` | Flughöhe im Flug nachregeln, wenn sie länger als 2 s um mehr als 8 cm vom Ziel abweicht – z. B. nachdem ein Auto unter der Drohne durchgefahren ist. Pausiert, solange ein Auto im Bild ist (Standard an) |
| `--max-area` | Flächen größer als dieser Bildanteil werden nicht als Linie gewertet (Standard 0.4) |
| `--height N` | Flughöhe in cm (40–100, Standard 55); die Seitenregler-Werte gelten für 55 cm und werden mit der Höhe skaliert |

Alle Optionen: `python line_follower.py --help`

## Tuning

| Verhalten | Lösung |
|---|---|
| Pendelt seitlich | `--k-lateral` verringern; Reaktion der Drohne mit `python step_test.py` messen |
| Kommt nicht vorwärts | `--speed` erhöhen – unter ~20 bewegt sich die Tello kaum |
| Schafft Kurven nicht | `--k-yaw` erhöhen oder `--speed` senken |
| Driftet in Kurven nach außen | `--k-curve` erhöhen (Standard 12); lenkt sie in Kurven nach innen über die Linie hinaus, verringern |
| Dreht ruckartig | `--k-yaw-d` erhöhen |
| Verliert die Linie oft | Flughöhe (`--height`) anpassen – höher sieht mehr vom Band, zu hoch wird dünnes Band zu schmal |

**Strecke:** schwarzes Klebeband (getestet mit 1,5 cm und 5 cm, durchgezogen oder gestrichelt) auf hellem,
gleichmäßigem Boden, gute Beleuchtung.
Die untere Kamera ist schwarzweiß (320×240) – es zählt nur der Hell-Dunkel-Kontrast, nicht die Farbe.

## Akku und Temperatur

- Die Drohne heizt sich vor allem **eingeschaltet am Boden** auf (kein Luftstrom). Zwischen Flügen ausschalten
  oder kühlen: `python cool.py --drone 108` (Kühlmodus bis 70 °C, Propeller drehen langsam, kein Abheben).
- Größter Wärmeerzeuger ist der **Videostream**: Am Boden (Motoren aus) stieg die Temperatur damit um ~2,2 °C/min
  (65 → 76,5 °C in 4 min), ohne Stream fiel sie wieder um ~1,7 °C/min. Die Mission-Pad-Erkennung allein macht
  kaum etwas aus (+0,5 statt +0,3 °C/min). Das Programm schaltet den Stream deshalb nach jedem Flug aus, und der
  Testmodus endet nach 3 min (`--test-limit`). Im Flug pendelt sich die Drohne bei ~75–78 °C ein.
- Ab ~85 °C fallen Video und Verbindung aus. Das Skript startet deshalb erst unter 80 °C (`--max-temp`).
- Im Flug landet das Skript bei 15 % Akku (`--land-battery`), gestartet wird erst ab 25 % (`--min-battery`).

## Bekannte Probleme

- **Überhitzung:** Am Boden mit laufender Kamera wird die Drohne schnell 70 °C+ warm. Dann antwortet sie
  zeitweise nicht mehr oder der Videostream startet nicht. Nicht lange eingeschaltet herumliegen lassen,
  bei Problemen ausschalten und abkühlen lassen.
- **Feste IP:** Die Tello hat keine Einstellung dafür; die IP vergibt der Router per DHCP. Eine feste Adresse
  geht nur über eine DHCP-Reservierung im Router (MAC der Drohne). Alternativ `--drone <Name>` verwenden.
- **Mission Pad am Linienanfang:** wird größtenteils ausgeblendet, kann die Erkennung beim Start aber noch
  stören – Drohne besser neben dem Pad direkt über dem Band starten.
- **Andere dunkle Objekte neben der Strecke** (z. B. Roboter): Die Erkennung wählt das Objekt, das wie ein Band
  aussieht (lang, gleich breit, dort wo die Linie im letzten Bild war). Berührt ein Objekt das Band, kann es
  trotzdem mit erfasst werden – Strecke möglichst frei halten.
- **Autos:** Die untere Kamera sieht bei 55 cm Flughöhe nur etwa 20–30 cm voraus – die Drohne hält kurz vor dem
  Auto, nicht schon von Weitem. Mit 5 cm breitem Band können Kreuzungen des Bandes kurz für ein Auto gehalten
  werden (Halt von ~1 s); mit dem 1,5-cm-Band der Smart City tritt das nicht auf. Fährt ein Auto direkt unter die
  schwebende Drohne, stört es ihre eigene Positionshaltung – sie kann dann abdriften.
- **Abzweigungen:** Die Drohne hält an, sobald die Kreuzung vorne im Bild sicher erkannt ist – also kurz
  davor, nicht genau darüber. Eine feste Route (z. B. "an der 2. Kreuzung links") gibt es noch nicht.
