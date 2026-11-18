# ptp-gm – GPS-gestützte PTP Grandmaster Clock + PTP-Messgerät

Raspberry Pi (CM4/Pi 4/Pi 5) mit **Intel I210** und **u-blox NEO-6M**.
Das PPS-Signal des GPS-Moduls wird direkt an die I210 geführt und dort von der
Hardware-Uhr (PHC) mit Zeitstempel versehen. Dadurch hängt die Genauigkeit nicht
an der Interrupt-Latenz des Pi.

```
NEO-6M ──NMEA (UART)──► gpsd ──► ts2phc ◄──PPS── I210 SDPx
                                     │
                                     ▼
                             I210-PHC (TAI) ──► ptp4l (Master, eth1) ──► PTP-Clients
                                     │
                                     └──► phc2sys ──► Systemzeit ──► chrony (NTP, optional)
```

Betriebsarten (umschaltbar im Web-UI):

| Modus | Dienste | Zweck |
|---|---|---|
| **Grandmaster** | gpsd, ts2phc, ptp4l (Master), chrony (oder phc2sys) | Zeitquelle im Netz |
| **Messgerät** | gpsd, ts2phc, ptp4l (nur Client, `free_running`) | Die PHC wird per GPS-PPS geführt, `ptp4l` misst Offset und Pfadverzögerung eines fremden Grandmasters dagegen, ohne eine Uhr zu verstellen. Messgerät- und PPS-Interface müssen dieselbe Karte sein. |
| **Aus** | nichts | |

Die Weboberfläche zeigt GPS-Status, PPS→PHC-Offset, PTP-Status, Verlaufsdiagramm
mit Statistik (Mittel, σ, RMS, Min/Max), CSV-Export, Konfiguration und Logs.

## Verkabelung

NEO-6M (3,3 V!, nicht an 5 V):

| NEO-6M | Pi-Header |
|---|---|
| VCC | Pin 1 (3,3 V) |
| GND | Pin 6 |
| TX | Pin 10 (RXD, GPIO15) |
| RX | Pin 8 (TXD, GPIO14) |
| **PPS** | **I210 SDP1** (Standard) + gemeinsame Masse |

Der PPS-Ausgang des NEO-6M liegt auf 3,3 V Pegel (ein kurzer Puls pro Sekunde,
nur aktiv mit Fix). Welche Lötpads/Stiftleiste die I210-Karte für SDP0–SDP3
herausführt, hängt vom Kartenmodell ab. Im Web-UI lassen sich Pin (0–3, Standard SDP1), Kanal
(0–1) und Flanke einstellen. Mit Fix prüfen: Das Log von `ts2phc` muss pro
Sekunde eine Zeile `master offset … s2` zeigen.

## Installation

```bash
git clone https://github.com/raindropmedia/ptp_GM.git
cd ptp_GM
sudo ./install.sh        # --no-boot-config überspringt die UART-Änderung
sudo reboot              # nur nötig, wenn der Installer die UART aktiviert hat
```

Der Installer
- installiert `linuxptp gpsd gpsd-clients chrony pps-tools ethtool`,
- kopiert die App nach `/opt/ptp-gm`, die systemd-Units nach `/etc/systemd/system`,
- legt ein zufälliges Web-Passwort an (Benutzer `admin`, wird einmal angezeigt),
- aktiviert die UART (`enable_uart=1`, `dtoverlay=disable-bt`), entfernt die serielle
  Konsole aus `cmdline.txt` (Backups: `*.ptpgm.bak`) und schaltet Bluetooth ab,
- deaktiviert `systemd-timesyncd`.

Danach unter `http://<pi>:8080/` einen Modus wählen. Passwort vergessen:
`cd /opt/ptp-gm && sudo python3 -m ptpgm set-password`.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `ptpgm/config.py` | Konfiguration mit strikter Validierung (nichts Ungeprüftes landet in Config-Dateien) |
| `ptpgm/render.py` | erzeugt `ptp4l.conf`, `ts2phc.conf`, `/etc/default/gpsd`, chrony-Snippet |
| `ptpgm/system.py` | systemd-Steuerung, Moduswechsel, NIC-/PHC-Infos |
| `ptpgm/status.py` | gpsd-Client, `pmc`-Abfragen, ts2phc-Offset, History (6 h, 1 Hz) |
| `ptpgm/server.py` | HTTP-Server (nur Standardbibliothek), Basic-Auth, JSON-API |
| `ptpgm/web/` | Oberfläche (ohne externe Abhängigkeiten, läuft offline) |
| `ptpgm/nmea_relay.py` | liefert `ts2phc` die NMEA-Zeilen von `gpsd` (siehe unten) |
| `systemd/` | Units `ptpgm-{nmea,ts2phc,ptp4l,phc2sys,web}.service` |
| `/etc/ptp-gm/` | `config.json`, `web.json` (Passwort-Hash), `generated/` |

Tests: `python3 -m unittest discover -s tests`

## Demo ohne Hardware

```bash
PTPGM_DEMO=1 PTPGM_PORT=8081 PTPGM_BIND=127.0.0.1 python3 -m ptpgm serve
```

Der Demo-Modus erzeugt synthetische GPS-, PPS- und PTP-Daten, braucht weder Root noch GPS
noch eine Gegenstelle und verändert nichts am System (Konfiguration landet in
`/tmp/ptpgm-demo`, kein Passwort nötig).

## Profil-Vorlagen

In den Einstellungen füllt die Vorlage Domain, Transport, Delay-Mechanismus und Intervalle:
IEEE 1588 Default, AES67, SMPTE ST 2059-2 und Layer 2 / Peer-to-Peer. Die Werte gelten für
Grandmaster und Messgerät und lassen sich danach einzeln ändern (die Vorlage springt auf
„Benutzerdefiniert“).

## NMEA-Relay und Systemzeit

- `ts2phc` 4.2 schickt kein `?WATCH` an `gpsd`, deshalb läuft `ptpgm-nmea` als Relay und gibt nur
  die RMC-Sätze als reinen NMEA-Strom auf `127.0.0.1:2948` aus (das doppelte Satzset von `gpsd` wird
  dabei auf eines reduziert).
- `ts2phc` schätzt die Zeit der PPS-Flanke als `RMC-Zeit + Zeit seit Empfang`. Mit beiden Flanken
  (I210) verwirft es jede Flanke, deren Schätzung nicht innerhalb ±½ Pulsbreite einer vollen Sekunde
  liegt. Der NEO-6M sendet den RMC erst etwa 135 ms nach dem Puls, daher wurde jede Flanke
  übersprungen (`SKIP extts … src …62`). Das Relay schiebt deshalb den Sekundenbruchteil der
  RMC-Zeit um diese Verzögerung (`hhmmss.00` → `hhmmss.135`). Die Sekunden kommen weiter vom GPS.
- Die Verzögerung steht in den Einstellungen („NMEA-Verzögerung“) und lässt sich messen
  (Systemzeit muss stimmen, z. B. per NTP): `cd /opt/ptp-gm && python3 -m ptpgm nmea-delay`.
  Im `ts2phc`-Debuglog (`-l 7`) zeigt `nmea delay` nach der Korrektur etwa 0 ms.
- **Systemzeit-Regler** (Einstellungen → System): Standard ist `chrony`. Es nutzt die GPS-geführte PHC
  der I210 als Refclock (`refclock PHC /dev/ptp1 tai poll 0 dpoll -2 refid GPS prefer`) und stellt
  damit die Systemzeit. Der Pi meldet sich dann per NTP als Stratum 1. Zusätzlich steht die
  NMEA-Zeit von `gpsd` als `refclock SHM 0 refid NMEA noselect` in der Quellentabelle, nur zum
  Vergleich (±100 ms, steuert nichts). Alternativen: `phc2sys` oder `none`.
- **NTP-Server:** Mit „NTP-Server“ (Standard an) beantwortet chrony NTP-Anfragen der PCs im Netz (UDP 123) aus der
  Systemzeit. Erlaubt ist das Subnetz der PTP-Karte (`eth1`) oder der Bereich aus „NTP erlaubt für“. Ohne
  `allow` antwortet chrony niemandem. Test von einem PC: `ntpdate -q <IP>` oder `chronyc -h <IP> tracking`.
- Die PHC-Quelle wird vom Web-Dienst erst eingeschaltet, wenn `ts2phc` 30 s lang im Zustand `s2`
  ist, und nach 2 Minuten ohne Lock wieder ausgeschaltet (chrony wird dabei neu gestartet). So folgt
  die Systemzeit nie einer noch freilaufenden PHC.
- Die I210 stempelt beide PPS-Flanken: Flanke `both` und die richtige Pulsbreite (NEO-6M: 100 ms).

## Diagnose: PPS prüfen

`tools/pps_probe.py` zeigt die rohen Flanken-Zeitstempel am PPS-Eingang der I210 (Root nötig,
vorher `ts2phc` stoppen):

```bash
sudo systemctl stop ptpgm-ts2phc
sudo python3 tools/pps_probe.py --seconds 10
```

Ein gesundes 1-Hz-PPS mit 100 ms Puls zeigt abwechselnd Abstände von etwa 100 ms und 900 ms
(beide Flanken). Keine Ausgabe heißt: kein PPS am Pin (Verkabelung, Pin, Masse).

## clockClass

Mit der Automatik (Standard) meldet der Grandmaster `clockClass` 6 nur, solange GPS-Fix und
PPS-Servo (`s2`) vorliegen. Danach 7 für höchstens 30 Minuten (Holdover), anschließend 248.
Beim Moduswechsel werden Dienste anderer Setups (`ptpweb-*`, `ptp4l-gm`, `phc2sys-gm` u. a.)
gestoppt und deaktiviert, weil sie sich Socket und PHC mit `ptp4l` teilen würden.

## Sicherheit

- Status, Verlauf, CSV und Logs sind ohne Anmeldung lesbar. Das Passwort (HTTP Basic Auth)
  braucht man nur für die Konfiguration: Konfiguration lesen/speichern, Modus wechseln,
  Anwenden, Verlauf löschen, Passwort ändern.
- Der Web-Dienst läuft als root (er startet Dienste und schreibt Konfiguration).
  Der Schutz besteht nur aus dem Passwort, **ohne TLS**. Nur im
  vertrauenswürdigen Netz betreiben oder einen Reverse-Proxy mit TLS davorsetzen.
- Schreibende Requests verlangen den Header `X-Requested-With` (CSRF-Schutz).
- Alle Eingaben werden gegen feste Wertebereiche geprüft, bevor sie in Dateien landen.

## Bekannte Grenzen / noch zu prüfen

- Auf der Entwicklungsmaschine war weder GPS angeschlossen noch konnten die Dienste
  installiert werden. Getestet sind Konfigurationsvalidierung, Rendering, die Parser
  und die HTTP-API (`tests/`). **Der Betrieb mit echter Hardware (ts2phc ↔ I210 ↔ PPS)
  ist noch nicht verifiziert.**
- `ts2phc` mit `-s nmea` und gpsd-Anbindung (`ts2phc.nmea_remote_host`) muss in der
  installierten linuxptp-Version unterstützt werden (Debian 13: 4.2). Prüfen mit
  `journalctl -u ptpgm-ts2phc`. Ist das nicht der Fall, im UI die Quelle auf `generic`
  stellen (Zeit-der-Sekunde aus der Systemzeit).
- Der NEO-6M liefert nur GPS, keine Quantisierungsfehler-Korrektur (sawtooth). Realistisch sind
  Offsets im Bereich einiger zehn Nanosekunden.
- Der UTC-Offset (TAI–UTC) ist fest konfiguriert (37 s) und wird nicht automatisch aus GPS gelesen.
- Der Onboard-Port `eth0` (Broadcom-PHY) wird bei der PHC-Erkennung noch nicht aufgelöst; Ziel ist die I210 (`eth1`).
