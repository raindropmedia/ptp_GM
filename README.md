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
| **Grandmaster** | gpsd, ts2phc, ptp4l (Master), phc2sys, optional chrony | Zeitquelle im Netz |
| **Messgerät** | ptp4l (nur Client, `free_running`) | misst Offset und Pfadverzögerung zu einem fremden Grandmaster, ohne die lokale Uhr zu verändern |
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
| **PPS** | **I210 SDP0** (Standard) + gemeinsame Masse |

Der PPS-Ausgang des NEO-6M liegt auf 3,3 V Pegel (ein kurzer Puls pro Sekunde,
nur aktiv mit Fix). Welche Lötpads/Stiftleiste die I210-Karte für SDP0–SDP3
herausführt, hängt vom Kartenmodell ab. Im Web-UI lassen sich Pin (0–3), Kanal
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
| `systemd/` | Units `ptpgm-{ts2phc,ptp4l,gmflags,phc2sys,web}.service` |
| `/etc/ptp-gm/` | `config.json`, `web.json` (Passwort-Hash), `generated/` |

Tests: `python3 -m unittest discover -s tests`

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
