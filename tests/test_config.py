import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ptpgm import config, render

class T(unittest.TestCase):
    def test_defaults_valid(self):
        cfg = config.validate({})
        self.assertEqual(cfg["ptp"]["clock_class"], 6)
        for fn in (render.ptp4l_gm, render.ptp4l_measure, render.ts2phc,
                   render.gpsd_default, render.chrony_snippet):
            self.assertTrue(fn(cfg))

    def test_rejects_injection(self):
        for bad in ({"ptp": {"interface": "eth1\nfoo bar"}},
                    {"gps": {"device": "/dev/../etc/passwd"}},
                    {"ptp": {"priority1": 999}},
                    {"mode": "evil"},
                    {"system": {"ntp_allow": "1.2.3.4; rm -rf /"}},
                    {"ptp": {"clock_accuracy": "0x21\nx"}}):
            with self.assertRaises(config.ValidationError, msg=str(bad)):
                config.validate(bad)

    def test_render_content(self):
        cfg = config.validate({"ptp": {"priority1": 10}, "pps": {"pin": 2, "polarity": "falling"}})
        self.assertIn("priority1               10", render.ptp4l_gm(cfg))
        t = render.ts2phc(cfg)
        self.assertIn("ts2phc.pin_index        2", t)
        self.assertIn("falling", t)
        self.assertIn("[eth1]", t)

if __name__ == "__main__":
    unittest.main()
