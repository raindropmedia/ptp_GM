"""Live status collection: gpsd, pmc (ptp4l), ts2phc, chrony, plus a history ring."""
import json
import re
import socket
import threading
import time
from collections import deque

from . import system

PMC_COMMANDS = [
    "GET TIME_STATUS_NP", "GET CURRENT_DATA_SET", "GET PORT_DATA_SET",
    "GET PARENT_DATA_SET", "GET DEFAULT_DATA_SET", "GET GRANDMASTER_SETTINGS_NP",
]
HISTORY_LEN = 6 * 3600  # one sample per second


# ---------------------------------------------------------------- pmc
def parse_pmc(text):
    """Parse pmc output into {MANAGEMENT_ID: {key: value}}."""
    res, cur = {}, None
    for line in text.splitlines():
        m = re.search(r"RESPONSE MANAGEMENT (\w+)", line)
        if m:
            cur = res.setdefault(m.group(1), {})
            continue
        if cur is not None and line.startswith("\t\t"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                cur[parts[0]] = parts[1].strip()
            elif len(parts) == 1:
                cur[parts[0]] = ""
        elif line.startswith("sending:"):
            cur = None
    return res


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def query_ptp4l():
    rc, out, err = system.run(
        ["pmc", "-u", "-b", "0", "-i", "/var/run/ptpgm-pmc", "-s", "/var/run/ptp4l"] + PMC_COMMANDS,
        timeout=5)
    if rc != 0 and not out:
        return None
    d = parse_pmc(out)
    return d or None


def ptp_summary(d):
    """Flatten pmc data into the fields the UI needs."""
    if not d:
        return None
    ts, cur, port = d.get("TIME_STATUS_NP", {}), d.get("CURRENT_DATA_SET", {}), d.get("PORT_DATA_SET", {})
    par, dflt, gms = d.get("PARENT_DATA_SET", {}), d.get("DEFAULT_DATA_SET", {}), d.get("GRANDMASTER_SETTINGS_NP", {})
    return {
        "port_state": port.get("portState"),
        "peer_delay_ns": _num(port.get("peerMeanPathDelay")),
        "offset_ns": _num(cur.get("offsetFromMaster")),
        "path_delay_ns": _num(cur.get("meanPathDelay")),
        "steps_removed": _num(cur.get("stepsRemoved")),
        "master_offset_ns": _num(ts.get("master_offset")),
        "gm_present": ts.get("gmPresent"),
        "gm_identity": ts.get("gmIdentity") or par.get("grandmasterIdentity"),
        "parent_port": par.get("parentPortIdentity"),
        "gm_priority1": _num(par.get("grandmasterPriority1")),
        "gm_priority2": _num(par.get("grandmasterPriority2")),
        "gm_clock_class": _num(par.get("gm.ClockClass")),
        "gm_clock_accuracy": par.get("gm.ClockAccuracy"),
        "local_clock_identity": dflt.get("clockIdentity"),
        "local_clock_class": _num(dflt.get("clockClass")),
        "domain": _num(dflt.get("domainNumber")),
        "utc_offset": _num(gms.get("currentUtcOffset")),
        "utc_offset_valid": gms.get("currentUtcOffsetValid"),
        "ptp_timescale": gms.get("ptpTimescale"),
        "time_traceable": gms.get("timeTraceable"),
        "frequency_traceable": gms.get("frequencyTraceable"),
    }


def set_gm_flags(cfg):
    """Mark the grandmaster as traceable / valid UTC offset (GPS locked)."""
    t = cfg["ptp"]
    cmd = ("SET GRANDMASTER_SETTINGS_NP clockClass %d clockAccuracy %s "
           "offsetScaledLogVariance 0xffff currentUtcOffset %d leap61 0 leap59 0 "
           "currentUtcOffsetValid 1 ptpTimescale 1 timeTraceable 1 frequencyTraceable 1 timeSource %s"
           % (t["clock_class"], t["clock_accuracy"], t["utc_offset"], t["time_source"]))
    rc, out, err = system.run(
        ["pmc", "-u", "-b", "0", "-i", "/var/run/ptpgm-pmc-flags", "-s", "/var/run/ptp4l", cmd], timeout=5)
    return rc == 0 and "RESPONSE MANAGEMENT GRANDMASTER_SETTINGS_NP" in out


# ---------------------------------------------------------------- ts2phc
_TS2PHC_RE = re.compile(r"(\S+)\s+master offset\s+(-?\d+)\s+(s\d)\s+freq\s+([+-]?\d+)")


def parse_ts2phc(text):
    last = None
    for line in text.splitlines():
        m = _TS2PHC_RE.search(line)
        if m:
            last = {"iface": m.group(1), "offset_ns": int(m.group(2)),
                    "servo_state": m.group(3), "freq_ppb": int(m.group(4))}
    return last


# ---------------------------------------------------------------- chrony
def parse_chrony_tracking(text):
    d = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


# ---------------------------------------------------------------- gpsd
class GpsdClient(threading.Thread):
    def __init__(self, host="127.0.0.1", port=2947):
        super().__init__(daemon=True)
        self.host, self.port = host, port
        self.lock = threading.Lock()
        self.tpv, self.sky, self.connected = {}, {}, False
        self.pps = {}

    def run(self):
        while True:
            try:
                with socket.create_connection((self.host, self.port), timeout=5) as s:
                    s.settimeout(10)
                    s.sendall(b'?WATCH={"enable":true,"json":true,"pps":true}\n')
                    with self.lock:
                        self.connected = True
                    buf = b""
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            self._handle(line)
            except (OSError, socket.timeout):
                pass
            with self.lock:
                self.connected = False
            time.sleep(3)

    def _handle(self, line):
        try:
            o = json.loads(line)
        except ValueError:
            return
        with self.lock:
            c = o.get("class")
            if c == "TPV":
                self.tpv = o
            elif c == "SKY":
                self.sky = o
            elif c == "PPS":
                self.pps = o

    def snapshot(self):
        with self.lock:
            sats = self.sky.get("satellites") or []
            tpv = self.tpv
            return {
                "connected": self.connected,
                "mode": tpv.get("mode", 0),  # 0/1 no fix, 2 2D, 3 3D
                "time": tpv.get("time"),
                "lat": tpv.get("lat"), "lon": tpv.get("lon"), "alt": tpv.get("altMSL", tpv.get("alt")),
                "epx": tpv.get("epx"), "epy": tpv.get("epy"), "ept": tpv.get("ept"),
                "hdop": self.sky.get("hdop"), "pdop": self.sky.get("pdop"),
                "sats_seen": len(sats), "sats_used": sum(1 for s in sats if s.get("used")),
                "satellites": [{"prn": s.get("PRN"), "gnss": s.get("gnssid"), "el": s.get("el"),
                                "az": s.get("az"), "snr": s.get("ss"), "used": bool(s.get("used"))}
                               for s in sats],
                "pps_seen": bool(self.pps),
                "pps_qerr": self.pps.get("qErr"),
            }


# ---------------------------------------------------------------- collector
class Collector(threading.Thread):
    """Samples ptp4l/ts2phc once per second and keeps a bounded history."""

    def __init__(self, get_cfg):
        super().__init__(daemon=True)
        self.get_cfg = get_cfg
        self.gps = GpsdClient()
        self.history = deque(maxlen=HISTORY_LEN)
        self.lock = threading.Lock()
        self.ptp = None
        self.ts2phc = None
        self.chrony = None
        self.states = {}
        self._tick = 0

    def start(self):
        self.gps.start()
        super().start()

    def run(self):
        while True:
            t0 = time.time()
            try:
                self._sample()
            except Exception:  # never let the sampler die
                pass
            time.sleep(max(0.05, 1.0 - (time.time() - t0)))

    def _sample(self):
        self._tick += 1
        if self._tick % 5 == 1:
            self.states = system.services_state()
        cfg = self.get_cfg()
        ptp = None
        if self.states.get("ptp4l") == "active":
            ptp = ptp_summary(query_ptp4l())
        ts = None
        if cfg["mode"] == "grandmaster" and self.states.get("ts2phc") == "active":
            rc, out, _ = system.run(["journalctl", "-u", "ptpgm-ts2phc.service", "-n", "5",
                                     "--no-pager", "-o", "cat"], timeout=5)
            ts = parse_ts2phc(out)
        if self._tick % 5 == 1 and self.states.get("chrony") == "active":
            rc, out, _ = system.run(["chronyc", "tracking"], timeout=5)
            self.chrony = parse_chrony_tracking(out) if rc == 0 else None
        sample = {
            "t": round(time.time(), 3),
            "offset": (ptp or {}).get("master_offset_ns"),
            "delay": (ptp or {}).get("path_delay_ns"),
            "pps_offset": (ts or {}).get("offset_ns"),
            "state": (ptp or {}).get("port_state"),
        }
        with self.lock:
            self.ptp, self.ts2phc = ptp, ts
            if ptp or ts:
                self.history.append(sample)

    def snapshot(self):
        with self.lock:
            return {"ptp": self.ptp, "ts2phc": self.ts2phc, "chrony": self.chrony,
                    "services": dict(self.states), "gps": self.gps.snapshot()}

    def get_history(self, seconds=600):
        cutoff = time.time() - seconds
        with self.lock:
            return [s for s in self.history if s["t"] >= cutoff]

    def clear_history(self):
        with self.lock:
            self.history.clear()
