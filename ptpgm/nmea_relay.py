"""Relay NMEA RMC sentences from gpsd to ts2phc, compensating the receiver's output delay.

Why this exists (linuxptp 4.2, ts2phc -s nmea):
  * ts2phc never sends gpsd's ?WATCH, so it cannot talk to gpsd directly.
  * ts2phc estimates the time of the PPS edge as  src = RMC_time + (now - time_RMC_was_received).
    That is only correct if the RMC arrives right after the pulse. With both-edge
    timestamping (I210) it additionally drops every edge whose src is not within
    +/- pulsewidth/2 of a full second. A NEO-6M sends its RMC ~130 ms after the pulse,
    so every edge was skipped ("SKIP extts ... src ....62").
  * Fix: shift the time field of each RMC by the known delay (hhmmss.00 -> hhmmss.DDD).
    The seconds still come from the GPS; only the sub-second part is corrected.

The relay forwards only RMC sentences as a bare NMEA stream on 127.0.0.1:2948. gpsd (WATCH
nmea=true) delivers each sentence set twice per second (receiver passthrough first, then its
own regenerated copy ~250 ms later), so only the first RMC per time stamp is forwarded.
(WATCH raw=1 delivers nothing for NMEA devices.)
"""
import socket
import socketserver
import threading
import time

RELAY_HOST = "127.0.0.1"
RELAY_PORT = 2948
GPSD = ("127.0.0.1", 2947)
WATCH = b'?WATCH={"enable":true,"nmea":true}\n'
DEFAULT_DELAY_MS = 135


def checksum(body):
    """XOR of all bytes between '$' and '*'."""
    c = 0
    for b in body:
        c ^= b
    return c


def parse_rmc_time(line):
    """Return (seconds_of_day_int, millis) of a valid-looking RMC sentence or None."""
    if not line.startswith(b"$") or b"RMC," not in line[:8] or b"*" not in line:
        return None
    body, _, cs = line[1:].partition(b"*")
    try:
        if checksum(body) != int(cs[:2], 16):
            return None
    except ValueError:
        return None
    fields = body.split(b",")
    t = fields[1] if len(fields) > 1 else b""
    if len(t) < 6 or not t[:6].isdigit():
        return None
    ms = 0
    if b"." in t:
        frac = t.split(b".", 1)[1]
        if frac and frac.isdigit():
            ms = int((frac + b"000")[:3])
    return int(t[0:2]) * 3600 + int(t[2:4]) * 60 + int(t[4:6]), ms


def shift_rmc(line, delay_ms):
    """Add delay_ms to the sub-second part of an RMC time field. None if not a valid RMC
    or if the shift would cross into the next second."""
    parsed = parse_rmc_time(line)
    if parsed is None:
        return None
    sod, ms = parsed
    total = ms + int(delay_ms)
    if not 0 <= total < 1000:
        return None
    body = line[1:].partition(b"*")[0]
    fields = body.split(b",")
    t = fields[1]
    fields[1] = t[:6] + b".%03d" % total
    new_body = b",".join(fields)
    return b"$" + new_body + b"*%02X" % checksum(new_body)


def current_delay_ms():
    try:
        from . import config
        return config.load()["pps"]["nmea_delay_ms"]
    except Exception:
        return DEFAULT_DELAY_MS


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        delay = self.server.delay()
        try:
            with socket.create_connection(self.server.gpsd, timeout=5) as up:
                up.settimeout(30)
                up.sendall(WATCH)
                buf, last = b"", None
                while True:
                    chunk = up.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        stamp = parse_rmc_time(line)
                        if stamp is None or stamp == last:
                            continue  # not an RMC, or the duplicate copy
                        out = shift_rmc(line, delay)
                        if out:
                            last = stamp
                            self.request.sendall(out + b"\r\n")
        except OSError:
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr=(RELAY_HOST, RELAY_PORT), gpsd=GPSD, delay=current_delay_ms):
        self.gpsd = gpsd
        self.delay = delay if callable(delay) else (lambda: delay)
        super().__init__(addr, Handler)


def serve():
    srv = Server()
    print(f"NMEA relay on {RELAY_HOST}:{RELAY_PORT} <- gpsd {GPSD[0]}:{GPSD[1]}, "
          f"RMC delay compensation {current_delay_ms()} ms", flush=True)
    srv.serve_forever()


def start_background(addr, gpsd, delay=0):
    srv = Server(addr, gpsd, delay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def measure_delay(seconds=30, gpsd=GPSD):
    """Median delay between the full UTC second in the RMC and its arrival, using the
    system clock. Only meaningful if the system clock is accurate (e.g. NTP-synced)."""
    samples, last = [], None
    end = time.time() + seconds
    with socket.create_connection(gpsd, timeout=5) as up:
        up.settimeout(5)
        up.sendall(WATCH)
        buf = b""
        while time.time() < end:
            try:
                chunk = up.recv(4096)
            except socket.timeout:
                continue
            now = time.time()
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                p = parse_rmc_time(line.strip())
                if p and p != last:
                    last = p
                    delay = (now % 86400 - p[0] - p[1] / 1000) % 86400
                    if 0 <= delay < 1:
                        samples.append(delay * 1000)
    if not samples:
        return None
    samples.sort()
    return samples[len(samples) // 2], len(samples), samples[0], samples[-1]
