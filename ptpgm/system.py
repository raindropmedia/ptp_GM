"""Interaction with the OS: systemd, generated files, NIC information."""
import os
import re
import subprocess

from . import config, render

UNITS = {
    "gpsd": "gpsd.service",
    "ts2phc": "ptpgm-ts2phc.service",
    "ptp4l": "ptpgm-ptp4l.service",
    "phc2sys": "ptpgm-phc2sys.service",
    "chrony": "chrony.service",
}
CHRONY_SNIPPET = "/etc/chrony/conf.d/ptpgm.conf"
GPSD_DEFAULT = "/etc/default/gpsd"

# which units run in which mode
def wanted_units(cfg):
    mode = cfg["mode"]
    if mode == "grandmaster":
        u = {"gpsd", "ts2phc", "ptp4l"}
        if cfg["system"]["phc2sys"]:
            u.add("phc2sys")
        if cfg["system"]["ntp_server"]:
            u.add("chrony")
        return u
    if mode == "measure":
        return {"ptp4l"}
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


def write_system_files(cfg):
    """Files outside /etc/ptp-gm. Only written when the target dir exists."""
    if os.path.isdir(os.path.dirname(GPSD_DEFAULT)):
        config.atomic_write(GPSD_DEFAULT, render.gpsd_default(cfg))
    if os.path.isdir(os.path.dirname(CHRONY_SNIPPET)) and cfg["system"]["ntp_server"]:
        config.atomic_write(CHRONY_SNIPPET, render.chrony_snippet(cfg))
    elif os.path.exists(CHRONY_SNIPPET):
        os.unlink(CHRONY_SNIPPET)


def apply(cfg):
    """Render configs and bring systemd units in line with the mode."""
    log = []
    write_generated(cfg)
    write_system_files(cfg)
    want = wanted_units(cfg)
    # stop first (reverse dependency order), then start
    for name in ("phc2sys", "ptp4l", "ts2phc", "chrony", "gpsd"):
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
    for name in ("gpsd", "ts2phc", "ptp4l", "phc2sys", "chrony"):
        if name in want:
            unit = UNITS[name]
            rc, _, err = run(["systemctl", "enable", "--now", unit], timeout=30)
            log.append(f"started {unit}" if rc == 0 else f"FAILED {unit}: {err.strip()}")
    return log


def nic_info(iface):
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
    return info


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
    rc, out, err = run(["journalctl", "-u", unit, "-n", str(int(lines)), "--no-pager", "-o", "short-iso"], timeout=10)
    return out if rc == 0 else err
