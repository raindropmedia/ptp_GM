"""Demo mode (PTPGM_DEMO=1): synthetic data, nothing on the system is touched."""
import math
import random
import threading
import time
from collections import deque

from . import status, system

DEMO_PRNS = [2, 5, 7, 9, 13, 14, 17, 19, 20, 24, 28, 30, 120, 123]


class DemoGps:
    def __init__(self):
        self.sats = [{"prn": p, "gnss": 0, "az": random.uniform(0, 360), "el": random.uniform(8, 85),
                      "base": random.uniform(20, 46)} for p in DEMO_PRNS]
        self.t0 = time.time()

    def snapshot(self):
        t = time.time() - self.t0
        sats = []
        for s in self.sats:
            snr = max(8, s["base"] + 2.5 * math.sin(t / 7 + s["prn"]))
            sats.append({"prn": s["prn"], "gnss": s["gnss"], "el": round(s["el"]), "snr": round(snr),
                         "az": round((s["az"] + t * 0.004) % 360), "used": snr > 24 and s["el"] > 12})
        used = sum(1 for s in sats if s["used"])
        return {
            "connected": True, "mode": 3, "time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "lat": 51.2277 + random.gauss(0, 2e-6), "lon": 6.7735 + random.gauss(0, 2e-6),
            "alt": 38.2 + random.gauss(0, 0.4), "alt_hae": 83.5 + random.gauss(0, 0.4),
            "epx": 2.1, "epy": 2.4, "ept": 5e-8, "eph": 3.2, "epv": 4.9, "sep": 5.8, "eps": 0.3,
            "hdop": 0.9, "vdop": 1.3, "pdop": 1.6, "tdop": 1.1, "gdop": 2.0,
            "speed": 0.02, "track": 120.0, "climb": 0.0, "device": "/dev/ttyAMA0",
            "sats_seen": len(sats), "sats_used": used, "satellites": sats,
            "pps_seen": False, "pps_qerr": None,
        }


class DemoCollector(status.Collector):
    def __init__(self, get_cfg):
        super().__init__(get_cfg)
        self.gps = DemoGps()
        self.walk = 0.0
        self.freq = 1200.0
        self.off = 0.0

    def start(self):  # no real gpsd client
        threading.Thread.start(self)

    def _sample(self):
        cfg = self.get_cfg()
        want = system.wanted_units(cfg)
        self.states = {k: ("active" if k in want or k == "gpsd" else "inactive") for k in system.UNITS}
        self.states["gpsd"] = "active"
        self.walk += random.gauss(0, 1.5)
        self.walk *= 0.97
        self.freq += random.gauss(0, 3)
        mode = cfg["mode"]
        ts = ptp = None
        if mode in ("grandmaster", "measure"):
            ts = {"iface": cfg["pps"]["interface"], "offset_ns": round(self.walk + random.gauss(0, 4)),
                  "servo_state": "s2", "freq_ppb": round(self.freq)}
            self.gm = {"cc": 6, "locked": True}
        if mode == "grandmaster":
            ptp = self._ptp("MASTER", 0.0, 0.0, cfg)
        elif mode == "measure":
            off = 130 + 40 * math.sin(time.time() / 40) + random.gauss(0, 12)
            delay = 845 + random.gauss(0, 6)
            ptp = self._ptp("SLAVE", off, delay, cfg)
        if mode == "off":
            self.states["gpsd"] = "active"
        self.chrony = {"Reference ID": "47505300 (GPS)", "Stratum": "1", "System time": "0.000000031 seconds fast of NTP time",
                       "Last offset": "+0.000000012 seconds", "RMS offset": "0.000000040 seconds",
                       "Frequency": "3.412 ppm slow", "Leap status": "Normal"}
        self.chrony_sources = [
            {"mode": "#", "state": "*", "name": "GPS", "stratum": 0, "poll": 0, "reach": "377",
             "last_rx": 0, "offset": 3e-8, "error": 1e-7},
            {"mode": "#", "state": "?", "name": "NMEA", "stratum": 0, "poll": 4, "reach": "377",
             "last_rx": 3, "offset": -0.135, "error": 0.05}]
        sample = {"t": round(time.time(), 3), "offset": (ptp or {}).get("master_offset_ns") if mode == "measure" else None,
                  "delay": (ptp or {}).get("path_delay_ns") if mode == "measure" else None,
                  "pps_offset": (ts or {}).get("offset_ns"), "state": (ptp or {}).get("port_state"),
                  "freq": (ts or {}).get("freq_ppb")}
        with self.lock:
            self.ptp, self.ts2phc = ptp, ts
            if ptp or ts:
                self.history.append(sample)

    @staticmethod
    def _ptp(state, off, delay, cfg):
        t = cfg["ptp"] if state == "MASTER" else cfg["measure"]
        raw = {
            "PARENT_DATA_SET": {"parentPortIdentity": "a0b1c2.fffe.d3e4f5-1", "grandmasterIdentity": "a0b1c2.fffe.d3e4f5",
                                "grandmasterPriority1": "128", "gm.ClockClass": "6", "gm.ClockAccuracy": "0x21"},
            "PORT_DATA_SET": {"portState": state, "logMinDelayReqInterval": str(t["domain"] and 0),
                              "peerMeanPathDelay": "0", "delayMechanism": "1" if t["delay_mechanism"] == "E2E" else "2"},
            "CURRENT_DATA_SET": {"stepsRemoved": "0" if state == "MASTER" else "1",
                                 "offsetFromMaster": f"{off:.1f}", "meanPathDelay": f"{delay:.1f}"},
            "TIME_STATUS_NP": {"master_offset": str(round(off)), "gmPresent": "true", "gmIdentity": "a0b1c2.fffe.d3e4f5"},
            "DEFAULT_DATA_SET": {"clockIdentity": "001b21.fffe.f24f58", "domainNumber": str(t["domain"]), "clockClass": "6"},
            "GRANDMASTER_SETTINGS_NP": {"clockClass": "6", "currentUtcOffset": str(cfg["ptp"]["utc_offset"]),
                                        "currentUtcOffsetValid": "1", "ptpTimescale": "1", "timeTraceable": "1"},
        }
        return status.ptp_summary(raw)


def nic_info(iface):
    table = {
        "eth0": {"driver": "bcmgenet", "link": True, "speed": "1000Mb/s", "hw": True, "phc": None, "addrs": ["192.168.10.20/24"]},
        "eth1": {"driver": "igb", "link": True, "speed": "1000Mb/s", "hw": True, "phc": 1, "addrs": ["192.168.20.1/24"]},
        "wlan0": {"driver": "brcmfmac", "link": True, "speed": None, "hw": False, "phc": None, "addrs": ["192.168.10.21/24"]},
    }
    d = table.get(iface)
    if d is None:
        return {"name": iface, "exists": False}
    return {"name": iface, "exists": True, "state": "up", "speed": d["speed"], "link": d["link"],
            "hw_timestamping": d["hw"], "phc_index": d["phc"], "driver": d["driver"], "addrs": d["addrs"]}


def list_interfaces():
    return [nic_info(n) for n in ("eth0", "eth1", "wlan0")]


def phc_pins(index):
    return {"clock_name": "001b21f24f58", "n_external_timestamps": "2", "n_periodic_outputs": "2",
            "pps_available": "1", "pins": ["SDP0", "SDP1", "SDP2", "SDP3"]}


def journal(unit, lines=100):
    now = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime())
    return "\n".join(f"{now} demo {unit}[1]: demo mode - synthetic log line {i}" for i in range(5)) + "\n"


def hardware_check(cfg):
    return [
        {"name": "eth1 Hardware-Zeitstempel", "ok": True, "detail": "igb (Demo)"},
        {"name": "eth1 PPS-Eingang (EXTTS)", "ok": True, "detail": "2 Kanäle, Pins: SDP0, SDP1, SDP2, SDP3"},
        {"name": "GPS-Schnittstelle", "ok": True, "detail": "Demo"},
        {"name": "ptp4l", "ok": True, "detail": "Demo – nichts installiert nötig"},
    ]
