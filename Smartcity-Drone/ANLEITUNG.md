# Anleitung – die wichtigsten Befehle

Kurzanleitung für den Alltag mit der RoboMaster TT. Alle Details und Optionen stehen in der [README](README.md).

## 1. Einmalig: Projekt einrichten

```bash
git clone https://github.com/KARMA22194/tello-linefollower.git
cd tello-linefollower
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Bei jedem neuen Terminal zuerst wieder `source .venv/bin/activate` ausführen.

## 2. Einmalig pro Drohne: ins WLAN `SmartCity-Drohnen` bringen

1. Schalter am Erweiterungsmodul auf **AP**, Drohne einschalten.
2. Mac mit dem WLAN `RMTT-xxxxxx` der Drohne verbinden.
3. `python setup_wifi.py` ausführen. Es fragt nach dem WLAN-Passwort.
4. Drohne aus, Schalter auf **STA**, Drohne an, ca. 30 s warten.
5. Mac mit `SmartCity-Drohnen` verbinden, Drohne suchen (siehe unten) und ihre Seriennummer mit einem
   Kurznamen in `drones.json` eintragen.

## 3. Vor jedem Flug – Checkliste

| Was | Befehl / Prüfung |
|---|---|
| Mac im richtigen Netz? | `SmartCity-Drohnen` – oder Client-Netz, wenn `python net_check.py --drone 108` überall OK meldet |
| Drohne an und erreichbar? | `python find_drones.py 10.0.11.0/24` |
| Akku mindestens 25 %? | steht in der Ausgabe von `find_drones.py` |
| Drohne kühl (unter 80 °C)? | sonst `python cool.py --drone 108` |
| Strecke frei? | keine Gegenstände auf oder direkt neben dem Band |
| Erkennung ok? | Testmodus (siehe unten), die Linie muss grün umrandet sein |

## 4. Webinterface (statt Terminal)

```bash
python webapp.py
```

Dann im Browser `http://localhost:8000` öffnen. Das Dashboard hat drei Reiter:

- **Steuerung:** links die gefundenen Drohnen (Netz eintragen, **Suchen**; Drohne anklicken = auswählen),
  in der Mitte der Flugstatus, rechts **Starten**, **Testmodus** (nicht abheben), **Landen**, **❄ Kühlen** und
  **⚠ NOTFALL** (alle landen) samt Startoptionen (u. a. **Flughöhe** 40–100 cm, Standard 55, und **Zufällige Richtung**: an Kreuzungen selbst
  auslosen statt fragen). **❄ Kühlen** startet den Kühlmodus wie `cool.py` (Propeller drehen
  langsam, kein Abheben) bis zur Temperatur unter „Kühlen bis“; **Kühlen stoppen** bricht ab. Darunter das Kamerabild der ausgewählten Drohne und das Steuerkreuz für
  Abzweigungen: ◀ links, ▲ geradeaus, ▶ rechts, ▼ hier landen – oder die Pfeiltasten. **Esc** lässt alle landen.
  Darunter die **Flip**-Knöpfe (vorwärts, rückwärts, links, rechts; Taste F = rückwärts): nur im Flug und ab 60 % Akku,
  die Drohne steigt dafür auf 1 m und sinkt danach zurück – mindestens 1,5 m Platz nach oben lassen.
  Fliegen mehrere, wählen die Knöpfe über dem Kamerabild die Drohne (⚠ = wartet an einer Abzweigung).
- **Systeminfo:** Akku, Höhe, Geschwindigkeit, Kurs, Temperatur, die gesendeten Steuerbefehle und der Versatz zur Linie.
- **Log:** alle Meldungen aller Drohnen (INFO / SUCCESS / WARNING / ERROR), zum Leeren oder Speichern als Datei.

Unter **Regler gegen Schwanken** lassen sich Seitenkorrektur (P), Dämpfung (D), Drift-Ausgleich (I) und die
**Kurven-Vorsteuerung** (lenkt in Kurven vorab nach innen, Standard 12, 0 = aus) für den nächsten Start einstellen; der Browser merkt sich die Werte, **Standardwerte** setzt sie zurück. Die benutzten
Werte stehen im Log und in `events.log`.

Vom Tablet oder Handy im selben WLAN: `DRONE_WEB_PASSWORD=<Passwort> python webapp.py --host 0.0.0.0` und
`http://<IP des Macs>:8000` öffnen (Benutzer `drohne`). Die IP des Macs zeigt `ipconfig getifaddr en0`.

## 5. Die wichtigsten Befehle

| Befehl | Wozu |
|---|---|
| `python find_drones.py` | Alle Drohnen im Drohnennetz (10.0.11.0/24) mit IP, Name und Akku anzeigen – geht auch aus dem Client-Netz |
| `python net_check.py --drone 108` | Prüft, ob Befehle, Status und Video zwischen Mac und Drohne durchkommen (ohne Abheben) |
| `python line_follower.py --drone 108 --dry-run --dashed` | **Testmodus:** nicht abheben, nur Erkennung anzeigen – Drohne in ca. 50 cm Höhe über das Band halten |
| `python line_follower.py --drone 108 --dashed --record` | **Fliegen** mit gestrichelter Linie und Aufzeichnung |
| `python line_follower.py --drone 108 --record` | Fliegen mit durchgezogener Linie |
| `python cool.py --drone 108` | Drohne am Boden abkühlen (Propeller drehen langsam, kein Abheben) |
| `python webapp.py` | Webinterface starten (siehe Abschnitt 4) |
| `python step_test.py --drone 108` | Messflug: kurze Seitenstöße, misst Verzögerung und Totzone der Seitensteuerung (1 m Platz zu beiden Seiten) |
| `python calibrate.py --drone 108` | Kameradrehung prüfen – nur bei einer neuen Drohne oder wenn das Bild verdreht ist |
| `python setup_wifi.py` | Drohne ins WLAN einbinden (siehe Abschnitt 2) |

`--drone 108` sucht die Drohne über ihre Seriennummer, weil sich die IP ändern kann. Für eine andere Drohne
einfach deren Namen aus `drones.json` nehmen, z. B. `--drone 111`.

## 6. Tasten im Videofenster

Die Tasten wirken nur, wenn das **Videofenster** aktiv ist (einmal hineinklicken), nicht das Terminal.

| Taste | Wirkung |
|---|---|
| `q` | landen und Programm beenden – **jederzeit** |
| `f` | Flip rückwärts (nur im Flug, ab 60 % Akku; steigt dafür auf 1 m) |
| `a` | an einer Abzweigung: links |
| `w` | an einer Abzweigung: geradeaus |
| `d` | an einer Abzweigung: rechts |
| `l` | an einer Abzweigung: hier landen |

An einer Kreuzung wird die Drohne langsamer, bleibt stehen und zeigt im Fenster die möglichen Richtungen an.
Ohne Antwort landet sie nach 30 s. Mit `--junction auto` fliegt sie ohne Nachfrage.

## 7. Was die Drohne von selbst macht

| Situation | Reaktion |
|---|---|
| Linie 2 s nicht zu sehen | landet |
| Mission Pad am Streckenende erkannt | bremst ab, fliegt genau über die Padmitte (max. 5 cm daneben, sonst bis zu 3× nachkorrigieren) und landet |
| Akku unter 15 % | landet |
| Flip nach links/rechts | schiebt sich danach zurück, bis die Linie wieder im Bild ist (max. 5 s) |
| Höhe weicht länger als 2 s um mehr als 8 cm ab (z. B. nachdem ein Auto unter ihr war) | steigt bzw. sinkt sanft zurück auf die eingestellte Flughöhe; solange ein Auto im Bild ist, wartet sie damit |
| Auto (Roboter) vorne auf der Linie | bleibt stehen, fliegt weiter, sobald es weg ist; nach 30 s landet sie |
| Bildstörungen (Klötzchen) oder kein Bild | schwebt, startet die Kamera neu (max. 3×), landet nach 8 s ohne brauchbares Bild |
| Falsches Bildformat beim Start | startet das Video bis zu 3× neu, sonst kein Abflug |
| Zu heiß (ab 80 °C) oder Akku unter 25 % | startet gar nicht erst |

## 8. Häufige Probleme

| Problem | Lösung |
|---|---|
| `find_drones.py` findet die Drohne nicht | Drohne aus- und einschalten, 30 s warten. Leuchtet nichts: Akku leer. Erscheint ein WLAN `RMTT-…`: Schalter steht auf AP oder die WLAN-Daten fehlen → Abschnitt 2 |
| „Drohne zu heiß“ | `python cool.py --drone 108` oder ausschalten und abkühlen lassen |
| Video startet nicht / „Bildformat 320x720“ | Programm neu starten; hilft das nicht, Drohne aus- und einschalten |
| Drohne verliert die Linie oft | Beleuchtung gleichmäßiger, Band dunkler bzw. mehr Kontrast, Strecke frei räumen |
| Drohne pendelt seitlich | `--k-lateral 15` (Standard 20); mit `python step_test.py --drone 108` neu vermessen |
| Drohne kommt nicht um Kurven | `--speed 20` |
| Tasten reagieren nicht | ins Videofenster klicken |

## 9. Aufnahmen

Mit `--record` landet jeder Flug in `recordings/<Datum>_<Uhrzeit>/`:

- `video.mp4` – Kamerabild mit Erkennung (grün = Linie, roter Pfeil = Ziel)
- `raw.mp4` – Rohbild ohne Einblendungen, zum Nachstellen mit geänderter Erkennung
- `events.log` – Ereignisse mit Zeit (Abzweigung, gewählte Richtung, Auto, Videostörung, Landegrund,
  Abweichung von der Padmitte beim Landen)
- `log.csv` – pro Bild: Linie erkannt, Versatz, Winkel, Steuerbefehle, Status (z. B. Warten an Abzweigung), Höhe, Akku, Temperatur, Bildfehler

Die Aufnahmen werden nicht mit hochgeladen (`.gitignore`). Zum Auswerten den ganzen Ordner weitergeben.
