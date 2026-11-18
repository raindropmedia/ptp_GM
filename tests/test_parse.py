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
