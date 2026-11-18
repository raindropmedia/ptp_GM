#!/bin/bash
# Installer for ptp-gm on Raspberry Pi OS / Debian. Run as root:  sudo ./install.sh
set -euo pipefail

BOOT_CONFIG=1
for a in "$@"; do
  case "$a" in
    --no-boot-config) BOOT_CONFIG=0 ;;
    -h|--help) echo "usage: sudo ./install.sh [--no-boot-config]"; exit 0 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
OPT=/opt/ptp-gm
ETC=/etc/ptp-gm

echo "== packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  linuxptp gpsd gpsd-clients chrony pps-tools ethtool python3 tzdata

echo "== files"
install -d "$OPT" "$ETC" "$ETC/generated"
rm -rf "$OPT/ptpgm"
cp -r "$SRC/ptpgm" "$OPT/ptpgm"
find "$OPT/ptpgm" -name __pycache__ -prune -exec rm -rf {} +
cp "$SRC"/systemd/*.service /etc/systemd/system/
systemctl daemon-reload

echo "== web password"
cd "$OPT"
if [ ! -f "$ETC/web.json" ]; then
  PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(12))')"
  python3 -m ptpgm set-password --user admin --password "$PW" >/dev/null
  echo "  Web UI login:  admin / $PW   (change it in the UI)"
else
  echo "  keeping existing password"
fi
[ -f "$ETC/config.json" ] || python3 - <<'PY'
from ptpgm import config
config.save({})
PY
chmod 750 "$ETC"

if [ "$BOOT_CONFIG" -eq 1 ]; then
  echo "== boot config (UART for GPS) - backups are *.ptpgm.bak"
  CFG=/boot/firmware/config.txt; CMD=/boot/firmware/cmdline.txt
  [ -f "$CFG" ] || { CFG=/boot/config.txt; CMD=/boot/cmdline.txt; }
  cp -n "$CFG" "$CFG.ptpgm.bak"; cp -n "$CMD" "$CMD.ptpgm.bak"
  grep -q '^enable_uart=1' "$CFG" || echo 'enable_uart=1' >> "$CFG"
  grep -q '^dtoverlay=disable-bt' "$CFG" || echo 'dtoverlay=disable-bt' >> "$CFG"
  # free the UART from the serial console
  sed -i -E 's/ ?console=(serial0|ttyAMA0|ttyS0),[0-9]+//g' "$CMD"
  systemctl disable --now serial-getty@ttyAMA0.service serial-getty@ttyS0.service 2>/dev/null || true
  systemctl disable --now hciuart.service bluetooth.service 2>/dev/null || true
  echo "  UART enabled; a reboot is required before /dev/ttyAMA0 carries the GPS"
fi

echo "== services"
systemctl enable --now ptpgm-web.service
systemctl disable --now systemd-timesyncd.service 2>/dev/null || true

IP="$(hostname -I | awk '{print $1}')"
cat <<MSG

Done. Open http://${IP:-<pi>}:8080/  (user: admin)
Wiring: see README.md. The services for grandmaster mode are started when you
select a mode in the web UI (Grandmaster / Messgerät).
MSG
