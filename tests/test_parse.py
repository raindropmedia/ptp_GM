import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ptpgm import status

PMC = """sending: GET TIME_STATUS_NP
\t001b21.fffe.f24f58-0 seq 0 RESPONSE MANAGEMENT TIME_STATUS_NP
\t\tmaster_offset              -12
\t\tingress_time               1700000000123456789
\t\tgmPresent                  true
\t\tgmIdentity                 aabbcc.fffe.ddeeff
sending: GET CURRENT_DATA_SET
\t001b21.fffe.f24f58-0 seq 1 RESPONSE MANAGEMENT CURRENT_DATA_SET
\t\tstepsRemoved     1
\t\toffsetFromMaster -12.0
\t\tmeanPathDelay    845.0
sending: GET PORT_DATA_SET
\t001b21.fffe.f24f58-1 seq 2 RESPONSE MANAGEMENT PORT_DATA_SET
\t\tportState              SLAVE
\t\tpeerMeanPathDelay      0
"""

class T(unittest.TestCase):
    def test_pmc(self):
        s = status.ptp_summary(status.parse_pmc(PMC))
        self.assertEqual(s["master_offset_ns"], -12.0)
        self.assertEqual(s["path_delay_ns"], 845.0)
        self.assertEqual(s["port_state"], "SLAVE")
        self.assertEqual(s["gm_identity"], "aabbcc.fffe.ddeeff")

    def test_ts2phc(self):
        r = status.parse_ts2phc("ts2phc[1234.5]: eth1 master offset          -3 s2 freq      +1234\n")
        self.assertEqual(r, {"iface": "eth1", "offset_ns": -3, "servo_state": "s2", "freq_ppb": 1234})

    def test_chrony(self):
        d = status.parse_chrony_tracking("Reference ID    : 47505300 (GPS)\nStratum         : 1\n")
        self.assertEqual(d["Stratum"], "1")

if __name__ == "__main__":
    unittest.main()

class T2(unittest.TestCase):
    def test_chrony_sources(self):
        r = status.parse_chrony_sources("#,*,PPS,0,4,377,12,0.000000012,0.0000001,0.0000002\n^,-,ntp.example,2,6,377,30,0.001,0.0,0.002\n")
        self.assertEqual(r[0]["name"], "PPS")
        self.assertEqual(r[0]["state"], "*")
        self.assertEqual(r[1]["stratum"], 2.0)


class T3(unittest.TestCase):
    def test_gpsd_client_parses_messages(self):
        # regression: GpsdClient is a Thread; a method named _handle broke on Python 3.13
        g = status.GpsdClient()
        g._on_message(b'{"class":"TPV","mode":3,"lat":51.2,"lon":6.7,"time":"2026-11-18T17:00:00Z"}')
        g._on_message(b'{"class":"SKY","hdop":0.9,"satellites":[{"PRN":5,"ss":30,"used":true,"el":40,"az":100}]}')
        s = g.snapshot()
        self.assertEqual(s["mode"], 3)
        self.assertEqual(s["sats_used"], 1)
        self.assertEqual(s["hdop"], 0.9)


class T4(unittest.TestCase):
    def test_dop_only_sky_keeps_satellites(self):
        g = status.GpsdClient()
        g._on_message(b'{"class":"SKY","hdop":0.9,"satellites":[{"PRN":5,"ss":30,"used":true,"el":40,"az":100}]}')
        g._on_message(b'{"class":"SKY","hdop":1.1}')  # DOP-only message from gpsd
        s = g.snapshot()
        self.assertEqual(s["sats_seen"], 1)
        self.assertEqual(s["hdop"], 1.1)


class T5(unittest.TestCase):
    def test_ts2phc_real_line(self):
        # format printed by linuxptp 4.2 ts2phc: "/dev/ptp1 offset <ns> s2 freq <ppb>"
        r = status.parse_ts2phc("ts2phc[1830.302]: /dev/ptp1 offset         11 s2 freq  -24623\n")
        self.assertEqual(r, {"iface": "/dev/ptp1", "offset_ns": 11, "servo_state": "s2", "freq_ppb": -24623})
