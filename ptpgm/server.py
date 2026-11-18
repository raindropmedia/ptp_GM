"""Small stdlib HTTP server: JSON API + static single page UI."""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, config, render, status, system

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
LOG_UNITS = {"gpsd": "gpsd.service", "ts2phc": "ptpgm-ts2phc.service", "ptp4l": "ptpgm-ptp4l.service",
             "phc2sys": "ptpgm-phc2sys.service", "chrony": "chrony.service", "web": "ptpgm-web.service"}
ITER = 200_000


# ------------------------------------------------------------ auth
def hash_password(password, salt=None, iterations=ITER):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
    return {"salt": salt, "hash": h, "iterations": iterations}


def set_password(user, password):
    rec = hash_password(password)
    rec["user"] = user
    config.atomic_write(config.AUTH_FILE, json.dumps(rec) + "\n", 0o600)


def check_auth(header):
    if config.DEMO:
        return True
    try:
        with open(config.AUTH_FILE) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None  # no credentials configured
    if not header or not header.startswith("Basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return False
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(rec["salt"]), rec["iterations"]).hex()
    return hmac.compare_digest(user, rec["user"]) & hmac.compare_digest(h, rec["hash"])


# ------------------------------------------------------------ app
class App:
    def __init__(self):
        self.cfg_lock = threading.Lock()
        self._cfg = config.load()
        if config.DEMO:
            from . import demo
            self.collector = demo.DemoCollector(lambda: self._cfg)
        else:
            self.collector = status.Collector(lambda: self._cfg)

    @property
    def cfg(self):
        return self._cfg

    def set_cfg(self, new):
        with self.cfg_lock:
            self._cfg = config.save(new)
            return self._cfg

    def status(self):
        s = self.collector.snapshot()
        cfg = self._cfg
        iface = cfg["ptp"]["interface"] if cfg["mode"] != "measure" else cfg["measure"]["interface"]
        s.update({
            "version": __version__,
            "mode": cfg["mode"],
            "time": time.time(),
            "now": time.time(),
            "hostname": socket.gethostname(),
            "demo": config.DEMO,
            "utc_offset": cfg["ptp"]["utc_offset"],
            "nic": system.nic_info(iface),
            "pps_nic": system.nic_info(cfg["pps"]["interface"]),
        })
        return s


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ptp-gm"

        def log_message(self, fmt, *args):
            pass

        # -- helpers
        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            ok = check_auth(self.headers.get("Authorization"))
            if ok is None:
                self._send(503, {"error": "no web password set; run: sudo python3 -m ptpgm set-password"})
                return False
            if not ok:
                self._send(401, {"error": "authentication required"},
                           extra={"WWW-Authenticate": 'Basic realm="ptp-gm", charset="UTF-8"'})
                return False
            return True

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                raise ValueError("body too large")
            return json.loads(self.rfile.read(n) or b"{}")

        # -- routes
        def do_GET(self):
            u = urlparse(self.path)
            # Status, history and logs are public; only the configuration needs the password.
            if u.path in ("/api/config", "/api/render") and not self._auth():
                return
            q = parse_qs(u.query)
            if u.path in STATIC:
                fn = STATIC[u.path]
                with open(os.path.join(WEB_DIR, fn), "rb") as f:
                    return self._send(200, f.read(), MIME[os.path.splitext(fn)[1]])
            if u.path == "/api/status":
                return self._send(200, app.status())
            if u.path == "/api/config":
                return self._send(200, app.cfg)
            if u.path == "/api/presets":
                return self._send(200, config.PRESETS)
            if u.path == "/api/render":
                cfg = app.cfg
                return self._send(200, {
                    "ptp4l": render.ptp4l_measure(cfg) if cfg["mode"] == "measure" else render.ptp4l_gm(cfg),
                    "ts2phc": render.ts2phc(cfg), "gpsd": render.gpsd_default(cfg),
                    "phc2sys": "phc2sys " + " ".join(render.phc2sys_args(cfg))})
            if u.path == "/api/check":
                return self._send(200, system.hardware_check(app.cfg))
            if u.path == "/api/interfaces":
                out = system.list_interfaces()
                for i in out:
                    if i.get("phc_index") is not None:
                        i["phc"] = system.phc_pins(i["phc_index"])
                return self._send(200, out)
            if u.path in ("/api/history", "/api/history.csv"):
                try:
                    secs = max(1, min(6 * 3600, int(q.get("seconds", ["600"])[0])))
                except ValueError:
                    secs = 600
                h = app.collector.get_history(secs)
                if u.path.endswith(".csv"):
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    w.writerow(["unix_time", "offset_ns", "path_delay_ns", "pps_offset_ns", "port_state"])
                    for s in h:
                        w.writerow([s["t"], s["offset"], s["delay"], s["pps_offset"], s["state"]])
                    return self._send(200, buf.getvalue(), "text/csv",
                                      {"Content-Disposition": 'attachment; filename="ptp-history.csv"'})
                return self._send(200, h)
            if u.path == "/api/logs":
                unit = LOG_UNITS.get(q.get("unit", [""])[0])
                if not unit:
                    return self._send(400, {"error": "unknown unit"})
                return self._send(200, {"log": system.journal(unit, 200)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            # CSRF guard: browsers cannot set this header cross-origin without CORS preflight
            if self.headers.get("X-Requested-With") != "ptpgm":
                return self._send(403, {"error": "missing X-Requested-With header"})
            u = urlparse(self.path)
            try:
                body = self._json_body()
                if u.path == "/api/config":
                    new = config.merge(app.cfg, body)
                    return self._send(200, app.set_cfg(new))
                if u.path == "/api/apply":
                    cfg = app.cfg
                    log = system.apply(cfg)
                    return self._send(200, {"log": log})
                if u.path == "/api/mode":
                    new = config.merge(app.cfg, {"mode": body.get("mode")})
                    cfg = app.set_cfg(new)
                    app.collector.clear_history()
                    return self._send(200, {"mode": cfg["mode"], "log": system.apply(cfg)})
                if u.path == "/api/history/clear":
                    app.collector.clear_history()
                    return self._send(200, {"ok": True})
                if u.path == "/api/password":
                    pw = body.get("password", "")
                    if not isinstance(pw, str) or len(pw) < 8:
                        return self._send(400, {"error": "password must have at least 8 characters"})
                    with open(config.AUTH_FILE) as f:
                        user = json.load(f)["user"]
                    set_password(user, pw)
                    return self._send(200, {"ok": True})
            except config.ValidationError as e:
                return self._send(400, {"error": str(e)})
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})

    return Handler


def serve():
    app = App()
    app.collector.start()
    cfg = dict(app.cfg["web"])
    cfg["port"] = int(os.environ.get("PTPGM_PORT", cfg["port"]))
    cfg["bind"] = os.environ.get("PTPGM_BIND", cfg["bind"])
    srv = ThreadingHTTPServer((cfg["bind"], cfg["port"]), make_handler(app))
    if config.DEMO:
        print("DEMO MODE: synthetic data, the system is not modified", flush=True)
    print(f"ptp-gm web UI on http://{cfg['bind']}:{cfg['port']}/", flush=True)
    srv.serve_forever()
