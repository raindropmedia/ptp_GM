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


def query_ptp4l(domain=0):
    rc, out, err = system.run(
        ["pmc", "-u", "-b", "0", "-d", str(domain), "-i", "/var/run/ptpgm-pmc", "-s", "/var/run/ptp4l"] + PMC_COMMANDS,
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
        "raw": d,
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


HOLDOVER_S = 1800


def gm_class(cfg, locked, last_locked, now):
    """clockClass etc. from GPS lock state: 6 locked, 7 holdover (<=30 min), 248 free-running."""
    t = cfg["ptp"]
    if not t["auto_clock_class"]:
        cc = t["clock_class"]
        return cc, t["clock_accuracy"], t["time_source"], int(cc in (6, 7))
    if locked:
        return 6, "0x21", "0x20", 1
    if last_locked and now - last_locked < HOLDOVER_S:
        return 7, "0x23", "0x20", 1
    return 248, "0xfe", "0xa0", 0


def set_gm_flags(cfg, cc=None, acc=None, src=None, traceable=1):
    """Set GRANDMASTER_SETTINGS_NP on the running ptp4l (UTC valid, traceability, class)."""
    t = cfg["ptp"]
    cc = t["clock_class"] if cc is None else cc
    acc = t["clock_accuracy"] if acc is None else acc
    src = t["time_source"] if src is None else src
    cmd = ("SET GRANDMASTER_SETTINGS_NP clockClass %d clockAccuracy %s "
           "offsetScaledLogVariance 0x4e5d currentUtcOffset %d leap61 0 leap59 0 "
           "currentUtcOffsetValid 1 ptpTimescale 1 timeTraceable %d frequencyTraceable %d timeSource %s"
           % (cc, acc, t["utc_offset"], traceable, traceable, src))
    rc, out, err = system.run(
        ["pmc", "-u", "-b", "0", "-d", str(t["domain"]), "-i", "/var/run/ptpgm-pmc-flags",
         "-s", "/var/run/ptp4l", cmd], timeout=5)
    return rc == 0 and "RESPONSE MANAGEMENT GRANDMASTER_SETTINGS_NP" in out


# ---------------------------------------------------------------- ts2phc
_TS2PHC_RE = re.compile(r"(\S+)\s+(?:master )?offset\s+(-?\d+)\s+(s\d)\s+freq\s+([+-]?\d+)")


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


def parse_chrony_sources(csv_text):
    """chronyc -c sources: mode,state,name,stratum,poll,reach,lastrx,offset,err"""
    res = []
    for line in csv_text.splitlines():
        f = line.split(",")
        if len(f) < 10:
            continue
        res.append({"mode": f[0], "state": f[1], "name": f[2], "stratum": _num(f[3]),
                    "poll": _num(f[4]), "reach": f[5], "last_rx": _num(f[6]),
                    "offset": _num(f[7]), "error": _num(f[9])})
    return res


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
                            self._on_message(line)
            except (OSError, socket.timeout):
                pass
            with self.lock:
                self.connected = False
            time.sleep(3)

    def _on_message(self, line):
        try:
            o = json.loads(line)
        except ValueError:
            return
        with self.lock:
            c = o.get("class")
            if c == "TPV":
                self.tpv = o
            elif c == "SKY":
                # gpsd sends DOP-only SKY messages between the full ones (with "satellites"):
                # merge instead of replace, otherwise the satellite list is lost 3 of 4 times.
                self.sky.update(o)
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
                "vdop": self.sky.get("vdop"), "tdop": self.sky.get("tdop"), "gdop": self.sky.get("gdop"),
                "alt_hae": tpv.get("altHAE"), "speed": tpv.get("speed"), "track": tpv.get("track"),
                "climb": tpv.get("climb"), "eph": tpv.get("eph"), "epv": tpv.get("epv"), "sep": tpv.get("sep"),
                "eps": tpv.get("eps"), "device": tpv.get("device"),
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
        self.chrony_sources = []
        self.states = {}
        self._tick = 0
        self.last_locked = 0.0
        self.gm = {"cc": None, "locked": False}
        self._sent = (None, 0.0)
        self._lock_since = None
        self._servo_ok_at = 0.0

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
            ptp = ptp_summary(query_ptp4l(cfg["ptp"]["domain"] if cfg["mode"] != "measure" else cfg["measure"]["domain"]))
        ts = None
        if cfg["mode"] == "grandmaster" and self.states.get("ts2phc") == "active":
            rc, out, _ = system.run(["journalctl", "-u", "ptpgm-ts2phc.service", "-n", "5",
                                     "--no-pager", "-o", "cat"], timeout=5)
            ts = parse_ts2phc(out)
        if self._tick % 5 == 1 and self.states.get("chrony") == "active":
            rc, out, _ = system.run(["chronyc", "tracking"], timeout=5)
            self.chrony = parse_chrony_tracking(out) if rc == 0 else None
            rc, out, _ = system.run(["chronyc", "-c", "sources"], timeout=5)
            self.chrony_sources = parse_chrony_sources(out) if rc == 0 else []
        if cfg["mode"] == "grandmaster" and ptp is not None:
            self._gm_flags(cfg, ts)
        if self._tick % 5 == 0:
            self._manage_clock(cfg, ts)
        sample = {
            "t": round(time.time(), 3),
            "offset": (ptp or {}).get("master_offset_ns"),
            "delay": (ptp or {}).get("path_delay_ns"),
            "pps_offset": (ts or {}).get("offset_ns"),
            "state": (ptp or {}).get("port_state"),
            "freq": (ts or {}).get("freq_ppb"),
        }
        with self.lock:
            self.ptp, self.ts2phc = ptp, ts
            if ptp or ts:
                self.history.append(sample)

    def _manage_clock(self, cfg, ts):
        """Let the system clock follow the PHC only while ts2phc has it locked.

        Lock must hold for 30 s before the clock servo (chrony PHC refclock or phc2sys) is
        enabled, and is released after 120 s without lock."""
        now = time.time()
        disc = cfg["system"]["discipline"]
        active = cfg["mode"] in ("grandmaster", "measure")
        locked = bool(active and ts and ts["servo_state"] == "s2")
        if locked:
            self._servo_ok_at = now
            self._lock_since = self._lock_since or now
        else:
            self._lock_since = None
        held = locked and now - self._lock_since >= 30
        lost = not active or now - self._servo_ok_at > 120

        running = self.states.get("phc2sys") == "active"
        if disc == "phc2sys":
            if held and not running:
                system.run(["systemctl", "start", system.UNITS["phc2sys"]], timeout=15)
            elif running and lost:
                system.run(["systemctl", "stop", system.UNITS["phc2sys"]], timeout=15)
        elif running:  # switched away from phc2sys: make sure it is not left running
            system.run(["systemctl", "stop", system.UNITS["phc2sys"]], timeout=15)

        if "chrony" in system.wanted_units(cfg):
            enabled = system.chrony_phc_enabled()
            want = disc == "chrony" and ((enabled and not lost) or held)
            if want != enabled and system.write_chrony(cfg, want):
                system.run(["systemctl", "restart", system.UNITS["chrony"]], timeout=30)

    def _gm_flags(self, cfg, ts):
        now = time.time()
        locked = bool(ts and ts["servo_state"] == "s2" and self.gps.snapshot()["mode"] >= 2)
        if locked:
            self.last_locked = now
        cc, acc, src, tr = gm_class(cfg, locked, self.last_locked, now)
        key = (cc, acc, src, cfg["ptp"]["utc_offset"])
        self.gm = {"cc": cc, "locked": locked}
        if key == self._sent[0] and now - self._sent[1] < 30:
            return
        if set_gm_flags(cfg, cc, acc, src, tr):
            self._sent = (key, now)

    def snapshot(self):
        with self.lock:
            return {"gm": dict(self.gm), "ptp": self.ptp, "ts2phc": self.ts2phc, "chrony": self.chrony,
                    "chrony_sources": list(self.chrony_sources),
                    "services": dict(self.states), "gps": self.gps.snapshot()}

    def get_history(self, seconds=600):
        cutoff = time.time() - seconds
        with self.lock:
            return [s for s in self.history if s["t"] >= cutoff]

    def clear_history(self):
        with self.lock:
            self.history.clear()
