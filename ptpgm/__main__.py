import argparse
import getpass
import json
import sys

from . import config, render, server, status, system


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ptpgm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the web UI")
    sub.add_parser("apply", help="render configs and (re)start services for the saved mode")
    sub.add_parser("render", help="print the generated config files")
    sub.add_parser("gmflags", help="set grandmaster traceability flags via pmc (used by systemd)")
    sp = sub.add_parser("set-password", help="set the web UI password")
    sp.add_argument("--user", default="admin")
    sp.add_argument("--password", help="omit to be prompted")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        server.serve()
    elif a.cmd == "apply":
        for line in system.apply(config.load()):
            print(line)
    elif a.cmd == "render":
        cfg = config.load()
        print("## ptp4l.conf\n" + (render.ptp4l_measure(cfg) if cfg["mode"] == "measure" else render.ptp4l_gm(cfg)))
        print("## ts2phc.conf\n" + render.ts2phc(cfg))
        print("## /etc/default/gpsd\n" + render.gpsd_default(cfg))
        print("## chrony\n" + render.chrony_snippet(cfg))
    elif a.cmd == "gmflags":
        import time
        cfg = config.load()
        for _ in range(60):
            if status.set_gm_flags(cfg):
                print("grandmaster flags set")
                return 0
            time.sleep(1)
        print("could not set grandmaster flags", file=sys.stderr)
        return 1
    elif a.cmd == "set-password":
        pw = a.password or getpass.getpass("New password: ")
        if len(pw) < 8:
            print("password must have at least 8 characters", file=sys.stderr)
            return 1
        server.set_password(a.user, pw)
        print("password set for user", a.user)
    return 0


if __name__ == "__main__":
    sys.exit(main())
