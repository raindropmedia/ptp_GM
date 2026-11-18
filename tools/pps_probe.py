#!/usr/bin/env python3
"""Show raw PPS edge timestamps from a PHC's external timestamp input (needs root).

Stop ts2phc first:  sudo systemctl stop ptpgm-ts2phc
Run:                sudo python3 tools/pps_probe.py [--dev /dev/ptp1] [--pin 1] [--chan 0] [--seconds 10]

Prints each edge with the time since the previous edge. A healthy 1 Hz PPS with a 100 ms
pulse shows alternating gaps of ~100 ms and ~900 ms (both edges) or 1000 ms (one edge).
"""
import argparse
import fcntl
import os
import select
import struct
import time

PTP_PIN_SETFUNC2 = 0x40603D10   # _IOW('=', 16, struct ptp_pin_desc[96 bytes])
PTP_EXTTS_REQUEST2 = 0x40103D0B  # _IOW('=', 11, struct ptp_extts_request[16 bytes])
ENABLE, RISING, FALLING, STRICT = 1, 2, 4, 8
FUNC_EXTTS = 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default="/dev/ptp1")
    ap.add_argument("--pin", type=int, default=1)
    ap.add_argument("--chan", type=int, default=0)
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--edges", choices=["both", "rising", "falling"], default="both")
    a = ap.parse_args()

    fd = os.open(a.dev, os.O_RDWR | os.O_NONBLOCK)
    pin = struct.pack("64sIII20x", b"", a.pin, FUNC_EXTTS, a.chan)
    fcntl.ioctl(fd, PTP_PIN_SETFUNC2, pin)
    flags = ENABLE | STRICT | {"both": RISING | FALLING, "rising": RISING, "falling": FALLING}[a.edges]
    fcntl.ioctl(fd, PTP_EXTTS_REQUEST2, struct.pack("IIII", a.chan, flags, 0, 0))
    print(f"listening on {a.dev} SDP{a.pin} chan {a.chan} for {a.seconds}s (edges: {a.edges}) ...")

    n, prev, end = 0, None, time.time() + a.seconds
    try:
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            data = os.read(fd, 32 * 16)
            for off in range(0, len(data) - 31, 32):
                sec, nsec, _, idx, fl, _, _ = struct.unpack("qIIIIII", data[off:off + 32])
                t = sec + nsec / 1e9
                gap = "" if prev is None else f"  +{(t - prev) * 1000:9.3f} ms"
                print(f"{sec}.{nsec:09d}{gap}")
                prev, n = t, n + 1
    finally:
        fcntl.ioctl(fd, PTP_EXTTS_REQUEST2, struct.pack("IIII", a.chan, 0, 0, 0))
        os.close(fd)
    print(f"{n} edges in {a.seconds}s" + ("  -> NO PPS SEEN: check wiring/pin/ground" if n == 0 else ""))


if __name__ == "__main__":
    main()
