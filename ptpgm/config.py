"""Configuration store with strict validation.

Every value that ends up in a generated config file or a command line is
validated here, so the web UI cannot be used to inject arbitrary content.
"""
import copy
import json
import os
import re
import tempfile

ETC = os.environ.get("PTPGM_ETC", "/etc/ptp-gm")
CONFIG_FILE = os.path.join(ETC, "config.json")
AUTH_FILE = os.path.join(ETC, "web.json")
GEN_DIR = os.path.join(ETC, "generated")

DEFAULTS = {
    "mode": "grandmaster",            # grandmaster | measure | off
    "gps": {
        "device": "/dev/ttyAMA0",
        "baud": 9600,                 # NEO-6M factory default
    },
    "pps": {
        "interface": "eth1",          # NIC whose PHC receives the GPS PPS
        "pin": 0,                     # SDP pin index on the I210 (SDP0..SDP3)
        "channel": 0,                 # EXTTS channel (I210 has 0 and 1)
        "polarity": "rising",         # rising | falling | both
        "correction_ns": 0,           # cable / antenna delay compensation
        "source": "nmea",             # nmea (time of second from gpsd) | generic
    },
    "ptp": {
        "interface": "eth1",
        "domain": 0,
        "transport": "UDPv4",         # UDPv4 | UDPv6 | L2
        "delay_mechanism": "E2E",     # E2E | P2P
        "time_stamping": "hardware",  # hardware | software
        "priority1": 128,
        "priority2": 128,
        "clock_class": 6,             # 6 = locked to primary reference (GNSS)
        "clock_accuracy": "0x21",     # 0x21 = within 100 ns
        "time_source": "0x20",        # 0x20 = GNSS
        "utc_offset": 37,
        "log_announce_interval": 1,
        "log_sync_interval": 0,
        "log_min_delay_req_interval": 0,
    },
    "measure": {
        "interface": "eth1",
        "domain": 0,
        "transport": "UDPv4",
        "delay_mechanism": "E2E",
        "time_stamping": "hardware",
        "free_running": True,         # never touch the PHC, only measure
    },
    "system": {
        "phc2sys": True,              # PHC -> system clock (grandmaster mode)
        "ntp_server": False,          # serve the system clock via chrony
        "ntp_allow": "",              # e.g. "192.168.0.0/16"
    },
    "web": {
        "bind": "0.0.0.0",
        "port": 8080,
    },
}


class ValidationError(ValueError):
    pass


def _int(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise ValidationError(f"{name}: integer required")
    v = int(v)
    if not lo <= v <= hi:
        raise ValidationError(f"{name}: must be between {lo} and {hi}")
    return v


def _enum(v, name, choices):
    if v not in choices:
        raise ValidationError(f"{name}: must be one of {', '.join(map(str, choices))}")
    return v


def _hex8(v, name):
    if not isinstance(v, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,2}", v):
        raise ValidationError(f"{name}: expected hex byte like 0x21")
    return "0x%02x" % int(v, 16)


def _iface(v, name):
    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", v):
        raise ValidationError(f"{name}: invalid interface name")
    return v


def _device(v, name):
    if not isinstance(v, str) or not re.fullmatch(r"/dev/[A-Za-z0-9_./-]{1,40}", v) or ".." in v:
        raise ValidationError(f"{name}: must be a /dev/... path")
    return v


def _bool(v, name):
    if not isinstance(v, bool):
        raise ValidationError(f"{name}: boolean required")
    return v


def _cidr(v, name):
    if v == "":
        return v
    if not isinstance(v, str) or not re.fullmatch(r"[0-9a-fA-F:.]{2,45}(/\d{1,3})?", v):
        raise ValidationError(f"{name}: expected network like 192.168.0.0/16")
    return v


def validate(cfg):
    """Return a normalised copy of cfg or raise ValidationError."""
    d = merge(DEFAULTS, cfg)
    out = copy.deepcopy(d)
    out["mode"] = _enum(d["mode"], "mode", ["grandmaster", "measure", "off"])

    g = d["gps"]
    out["gps"] = {
        "device": _device(g["device"], "gps.device"),
        "baud": _enum(g["baud"], "gps.baud", [4800, 9600, 19200, 38400, 57600, 115200]),
    }
    p = d["pps"]
    out["pps"] = {
        "interface": _iface(p["interface"], "pps.interface"),
        "pin": _int(p["pin"], "pps.pin", 0, 3),
        "channel": _int(p["channel"], "pps.channel", 0, 1),
        "polarity": _enum(p["polarity"], "pps.polarity", ["rising", "falling", "both"]),
        "correction_ns": _int(p["correction_ns"], "pps.correction_ns", -1_000_000, 1_000_000),
        "source": _enum(p["source"], "pps.source", ["nmea", "generic"]),
    }
    t = d["ptp"]
    out["ptp"] = {
        "interface": _iface(t["interface"], "ptp.interface"),
        "domain": _int(t["domain"], "ptp.domain", 0, 127),
        "transport": _enum(t["transport"], "ptp.transport", ["UDPv4", "UDPv6", "L2"]),
        "delay_mechanism": _enum(t["delay_mechanism"], "ptp.delay_mechanism", ["E2E", "P2P"]),
        "time_stamping": _enum(t["time_stamping"], "ptp.time_stamping", ["hardware", "software"]),
        "priority1": _int(t["priority1"], "ptp.priority1", 0, 255),
        "priority2": _int(t["priority2"], "ptp.priority2", 0, 255),
        "clock_class": _int(t["clock_class"], "ptp.clock_class", 0, 255),
        "clock_accuracy": _hex8(t["clock_accuracy"], "ptp.clock_accuracy"),
        "time_source": _hex8(t["time_source"], "ptp.time_source"),
        "utc_offset": _int(t["utc_offset"], "ptp.utc_offset", 0, 100),
        "log_announce_interval": _int(t["log_announce_interval"], "ptp.log_announce_interval", -3, 4),
        "log_sync_interval": _int(t["log_sync_interval"], "ptp.log_sync_interval", -7, 4),
        "log_min_delay_req_interval": _int(t["log_min_delay_req_interval"], "ptp.log_min_delay_req_interval", -7, 4),
    }
    m = d["measure"]
    out["measure"] = {
        "interface": _iface(m["interface"], "measure.interface"),
        "domain": _int(m["domain"], "measure.domain", 0, 127),
        "transport": _enum(m["transport"], "measure.transport", ["UDPv4", "UDPv6", "L2"]),
        "delay_mechanism": _enum(m["delay_mechanism"], "measure.delay_mechanism", ["E2E", "P2P"]),
        "time_stamping": _enum(m["time_stamping"], "measure.time_stamping", ["hardware", "software"]),
        "free_running": _bool(m["free_running"], "measure.free_running"),
    }
    s = d["system"]
    out["system"] = {
        "phc2sys": _bool(s["phc2sys"], "system.phc2sys"),
        "ntp_server": _bool(s["ntp_server"], "system.ntp_server"),
        "ntp_allow": _cidr(s["ntp_allow"], "system.ntp_allow"),
    }
    w = d["web"]
    out["web"] = {
        "bind": w["bind"] if isinstance(w["bind"], str) and re.fullmatch(r"[0-9a-fA-F:.]{2,45}", w["bind"])
        else (_ for _ in ()).throw(ValidationError("web.bind: invalid address")),
        "port": _int(w["port"], "web.port", 1, 65535),
    }
    return out


def merge(base, over):
    res = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(res.get(k), dict):
            res[k] = merge(res[k], v)
        else:
            res[k] = v
    return res


def load():
    try:
        with open(CONFIG_FILE) as f:
            return validate(json.load(f))
    except FileNotFoundError:
        return validate({})


def save(cfg):
    cfg = validate(cfg)
    atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2) + "\n", 0o640)
    return cfg


def atomic_write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
