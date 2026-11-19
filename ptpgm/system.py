"""Interaction with the OS: systemd, generated files, NIC information."""
import json
import os
import re
import subprocess
import time

from . import config, render

DEMO = config.DEMO

UNITS = {
    "gpsd": "gpsd.service",
    "nmea": "ptpgm-nmea.service",
    "ts2phc": "ptpgm-ts2phc.service",
    "ptp4l": "ptpgm-ptp4l.service",
    "phc2sys": "ptpgm-phc2sys.service",
    "chrony": "chrony.service",
}
# Units of other setups that would fight over ptp4l's port/socket and the PHC
CONFLICT_UNITS = [
    "ptpweb-ptp4l.service", "ptpweb-phc2sys.service",
    "ptp4l-gm.service", "phc2sys-gm.service", "ptp-gm-flags.path", "ptp-gm-flags.service",
    "ptp4l.service", "phc2sys.service", "ts2phc.service",
]
CHRONY_SNIPPET = "/etc/chrony/conf.d/ptpgm.conf"
GPSD_DEFAULT = "/etc/default/gpsd"

# which units run in which mode
def wanted_units(cfg):
    mode = cfg["mode"]
    if mode == "grandmaster":
        # phc2sys / the chrony PHC refclock are enabled by the web service only once ts2phc
        # has locked the PHC to GPS, so the system clock never follows a free-running PHC.
        u = {"gpsd", "nmea", "ts2phc", "ptp4l"}
        if cfg["system"]["ntp_server"] or cfg["system"]["discipline"] == "chrony":
            u.add("chrony")
        return u
    if mode == "measure":
        # The PHC must follow GPS, otherwise the measured offset has no reference.
        u = {"gpsd", "nmea", "ts2phc", "ptp4l"}
        if cfg["system"]["discipline"] == "chrony":
            u.add("chrony")
        return u
    return set()


def run(cmd, timeout=10, input=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timeout"


def unit_state(unit):
    if DEMO:
        return "inactive"
    rc, out, _ = run(["systemctl", "is-active", unit])
    return out.strip() or "unknown"


def services_state():
    return {k: unit_state(v) for k, v in UNITS.items()}


def write_generated(cfg):
    """Render all config files for the current config. Returns list of paths."""
    g = config.GEN_DIR
    files = {
        os.path.join(g, "ptp4l.conf"):
            render.ptp4l_gm(cfg) if cfg["mode"] != "measure" else render.ptp4l_measure(cfg),
        os.path.join(g, "ts2phc.conf"): render.ts2phc(cfg),
        os.path.join(g, "phc2sys.env"):
            "PHC2SYS_ARGS=" + " ".join(render.phc2sys_args(cfg)) + "\n",
    }
    for path, text in files.items():
        config.atomic_write(path, text)
    return list(files)


def phc_index_for(cfg):
    idx = nic_info(cfg["pps"]["interface"]).get("phc_index")
    return idx if idx is not None else 1


def chrony_phc_enabled():
    try:
        with open(CHRONY_SNIPPET) as f:
            return "refclock PHC" in f.read()
    except OSError:
        return False


def ntp_subnets(cfg):
    """IPv4 subnets of the PTP interface, used when no explicit ntp_allow is configured."""
    import ipaddress
    nets = []
    for a in nic_info(cfg["ptp"]["interface"]).get("addrs", []):
        try:
            n = ipaddress.ip_interface(a)
        except ValueError:
            continue
        if n.version == 4 and str(n.network) not in nets:
            nets.append(str(n.network))
    return nets


def write_chrony(cfg, phc_enabled):
    if not os.path.isdir(os.path.dirname(CHRONY_SNIPPET)):
        return False
    config.atomic_write(CHRONY_SNIPPET, render.chrony_snippet(
        cfg, phc_index_for(cfg), phc_enabled, ntp_subnets(cfg)))
    return True


RTC_BEGIN = "# >>> ptp-gm rtc (managed) >>>"
RTC_END = "# <<< ptp-gm rtc (managed) <<<"
# CM4 IO board: PCF85063A on the i2c bus shared with CSI/DSI (see Raspberry Pi docs)
RTC_LINES = ["dtparam=i2c_vc=on", "dtoverlay=i2c-rtc,pcf85063a,i2c_csi_dsi"]


def boot_config_path():
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.isfile(p):
            return p
    return None


def rtc_setup(enabled):
    """Idempotently add/remove the RTC overlay block in config.txt. Returns a log line or None."""
    path = boot_config_path()
    if not path:
        return "RTC: config.txt nicht gefunden"
    with open(path) as f:
        text = f.read()
    block = re.compile(r"\n?" + re.escape(RTC_BEGIN) + r".*?" + re.escape(RTC_END) + r"\n?", re.S)
    has = bool(block.search(text))
    if enabled == has:
        return None
    if not os.path.exists(path + ".ptpgm.bak"):
        with open(path + ".ptpgm.bak", "w") as f:
            f.write(text)
    if enabled:
        text = text.rstrip("\n") + "\n" + RTC_BEGIN + "\n" + "\n".join(RTC_LINES) + "\n" + RTC_END + "\n"
    else:
        text = block.sub("\n", text)
    config.atomic_write(path, text)
    return ("RTC aktiviert" if enabled else "RTC deaktiviert") + " in " + path + " - Neustart erforderlich"


def rtc_info():
    """State of the kernel RTC device."""
    info = {"present": os.path.exists("/dev/rtc0")}
    if info["present"]:
        try:
            with open("/sys/class/rtc/rtc0/name") as f:
                info["name"] = f.read().strip()
        except OSError:
            pass
        try:  # sysfs works without hwclock (moved to util-linux-extra in Debian 13)
            with open("/sys/class/rtc/rtc0/since_epoch") as f:
                info["time"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(f.read())))
        except (OSError, ValueError):
            pass
    return info


def write_system_files(cfg):
    """Files outside /etc/ptp-gm. Only written when the target dir exists."""
    if os.path.isdir(os.path.dirname(GPSD_DEFAULT)):
        config.atomic_write(GPSD_DEFAULT, render.gpsd_default(cfg))
    if "chrony" in wanted_units(cfg):
        write_chrony(cfg, False)  # PHC refclock is switched on later, once ts2phc is locked
    elif os.path.exists(CHRONY_SNIPPET):
        os.unlink(CHRONY_SNIPPET)


def apply(cfg):
    """Render configs and bring systemd units in line with the mode."""
    log = []
    write_generated(cfg)
    if DEMO:
        return ["Demo-Modus: Konfiguration erzeugt, am System wurde nichts verändert"]
    write_system_files(cfg)
    msg = rtc_setup(cfg["system"]["rtc"])
    if msg:
        log.append(msg)
    want = wanted_units(cfg)
    if want:
        for u in CONFLICT_UNITS:
            if unit_state(u) == "active" or run(["systemctl", "is-enabled", u])[1].strip() == "enabled":
                run(["systemctl", "disable", "--now", u], timeout=15)
                log.append(f"foreign unit {u} stopped and disabled")
    # stop first (reverse dependency order), then start
    for name in ("phc2sys", "ptp4l", "ts2phc", "nmea", "chrony", "gpsd"):
        unit = UNITS[name]
        if name not in want:
            if name in ("chrony", "gpsd"):
                # never disable distro services other things may use; just stop gpsd/chrony
                # only when they were started by us (i.e. we are leaving that mode)
                continue
            run(["systemctl", "disable", "--now", unit])
            log.append(f"stopped {unit}")
        else:
            run(["systemctl", "stop", unit])
    for name in ("gpsd", "nmea", "ts2phc", "ptp4l", "phc2sys", "chrony"):
        if name in want:
            unit = UNITS[name]
            rc, _, err = run(["systemctl", "enable", "--now", unit], timeout=30)
            log.append(f"started {unit}" if rc == 0 else f"FAILED {unit}: {err.strip()}")
    return log


def nic_info(iface):
    if DEMO:
        from . import demo
        return demo.nic_info(iface)
    info = {"name": iface, "exists": os.path.isdir(f"/sys/class/net/{iface}")}
    if not info["exists"]:
        return info
    try:
        with open(f"/sys/class/net/{iface}/operstate") as f:
            info["state"] = f.read().strip()
    except OSError:
        pass
    rc, out, _ = run(["ethtool", iface])
    m = re.search(r"Speed:\s*(\S+)", out)
    info["speed"] = m.group(1) if m else None
    m = re.search(r"Link detected:\s*(\S+)", out)
    info["link"] = (m.group(1) == "yes") if m else None
    rc, out, _ = run(["ethtool", "-T", iface])
    info["hw_timestamping"] = "hardware-transmit" in out and "hardware-receive" in out
    m = re.search(r"PTP Hardware Clock:\s*(\d+)", out)
    info["phc_index"] = int(m.group(1)) if m else None
    if info["phc_index"] is None:
        info["phc_index"] = _phc_by_netdev(iface)
    info["driver"] = _driver(iface)
    info["addrs"] = _addrs(iface)
    return info


def _addrs(iface):
    """IPv4/IPv6 addresses (with prefix length) of an interface."""
    rc, out, _ = run(["ip", "-j", "addr", "show", "dev", iface])
    if rc != 0:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    res = []
    for entry in data:
        for a in entry.get("addr_info", []):
            if a.get("scope") == "link" and a.get("family") == "inet6":
                continue  # skip fe80:: link-local noise
            res.append(f"{a['local']}/{a['prefixlen']}")
    return res


def _driver(iface):
    try:
        return os.path.basename(os.readlink(f"/sys/class/net/{iface}/device/driver"))
    except OSError:
        return None


def _phc_by_netdev(iface):
    try:
        for n in os.listdir(f"/sys/class/net/{iface}/device/ptp"):
            m = re.fullmatch(r"ptp(\d+)", n)
            if m:
                return int(m.group(1))
    except OSError:
        pass
    return None


def list_interfaces():
    if DEMO:
        from . import demo
        return demo.list_interfaces()
    res = []
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        names = []
    for n in names:
        if n == "lo":
            continue
        i = nic_info(n)
        res.append(i)
    return res


def phc_pins(index):
    if DEMO:
        from . import demo
        return demo.phc_pins(index)
    base = f"/sys/class/ptp/ptp{index}"
    d = {}
    for k in ("clock_name", "n_external_timestamps", "n_periodic_outputs", "pps_available"):
        try:
            with open(os.path.join(base, k)) as f:
                d[k] = f.read().strip()
        except OSError:
            pass
    try:
        d["pins"] = sorted(os.listdir(os.path.join(base, "pins")))
    except OSError:
        d["pins"] = []
    return d


def journal(unit, lines=100):
    if DEMO:
        from . import demo
        return demo.journal(unit, lines)
    rc, out, err = run(["journalctl", "-u", unit, "-n", str(int(lines)), "--no-pager", "-o", "short-iso"], timeout=10)
    return out if rc == 0 else err


def hardware_check(cfg):
    """Quick hardware / software sanity check for the settings page."""
    if DEMO:
        from . import demo
        return demo.hardware_check(cfg)
    items = []

    def add(name, ok, detail):
        items.append({"name": name, "ok": ok, "detail": detail})

    for key in ("ptp", "pps"):
        i = cfg[key]["interface"]
        n = nic_info(i)
        if not n["exists"]:
            add(f"Interface {i}", False, "existiert nicht")
            continue
        add(f"{i} Hardware-Zeitstempel", bool(n.get("hw_timestamping")), n.get("driver") or "")
        add(f"{i} Link", bool(n.get("link")), n.get("speed") or "kein Link")
        add(f"{i} PHC", n.get("phc_index") is not None,
            f"/dev/ptp{n['phc_index']}" if n.get("phc_index") is not None else "keine PTP-Hardware-Uhr")
        if n.get("phc_index") is not None:
            ph = phc_pins(n["phc_index"])
            add(f"{i} PPS-Eingang (EXTTS)", int(ph.get("n_external_timestamps", "0") or 0) > 0,
                f"{ph.get('n_external_timestamps', '0')} Kanäle, Pins: {', '.join(ph.get('pins', [])) or '-'}")
    if cfg["measure"]["interface"] != cfg["pps"]["interface"]:
        add("Messgerät-Interface = PPS-Interface", False,
            "im Messgerät-Modus muss ptp4l auf der Karte laufen, deren PHC per GPS-PPS geführt wird")
    dev = cfg["gps"]["device"]
    add(f"GPS-Schnittstelle {dev}", os.path.exists(dev), "vorhanden" if os.path.exists(dev) else "fehlt (UART aktiviert? Neustart?)")
    if cfg["system"]["rtc"]:
        r = rtc_info()
        add("RTC (/dev/rtc0)", r["present"],
            f"{r.get('name', '')} {r.get('time', '')}".strip() if r["present"]
            else "nicht vorhanden (Neustart nach Aktivierung nötig? Batterie/Board?)")
    for tool in ("ptp4l", "ts2phc", "phc2sys", "pmc", "gpsd", "chronyd"):
        rc, out, err = run([tool, "--version"] if tool != "gpsd" else ["gpsd", "-V"])
        txt = (out or err).strip().splitlines()
        add(tool, rc != 127, txt[0] if txt else ("nicht installiert" if rc == 127 else ""))
    return items
