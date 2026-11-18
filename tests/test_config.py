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


class T2(unittest.TestCase):
    def test_gm_class(self):
        from ptpgm import status
        cfg = config.validate({})
        self.assertEqual(status.gm_class(cfg, True, 0, 1000)[0], 6)
        self.assertEqual(status.gm_class(cfg, False, 900, 1000)[0], 7)
        self.assertEqual(status.gm_class(cfg, False, 100, 2000)[0], 248)
        self.assertEqual(status.gm_class(cfg, False, 0, 2000)[0], 248)
        fixed = config.validate({"ptp": {"auto_clock_class": False, "clock_class": 13}})
        self.assertEqual(status.gm_class(fixed, False, 0, 0)[0], 13)

    def test_measure_needs_gps(self):
        from ptpgm import system
        cfg = config.validate({"mode": "measure"})
        self.assertEqual(system.wanted_units(cfg), {"gpsd", "nmea", "ts2phc", "ptp4l", "chrony"})
        self.assertNotIn("phc2sys", system.wanted_units(cfg))
        none = config.validate({"mode": "measure", "system": {"discipline": "none"}})
        self.assertEqual(system.wanted_units(none), {"gpsd", "nmea", "ts2phc", "ptp4l"})


class T3(unittest.TestCase):
    def test_presets_valid(self):
        for name, p in config.PRESETS.items():
            cfg = config.validate({"ptp": {k: v for k, v in p.items() if k != "label"} | {"preset": name}})
            self.assertEqual(cfg["ptp"]["preset"], name)
            self.assertEqual(cfg["ptp"]["domain"], p["domain"])
        self.assertEqual(config.validate({"ptp": {"preset": "bogus"}})["ptp"]["preset"], "custom")

    def test_render_preset_values(self):
        from ptpgm import render
        p = config.PRESETS["aes67"]
        cfg = config.validate({"ptp": {k: v for k, v in p.items() if k != "label"}})
        out = render.ptp4l_gm(cfg)
        self.assertIn("logSyncInterval         -3", out)


class T4(unittest.TestCase):
    def test_i210_defaults(self):
        from ptpgm import render
        cfg = config.validate({})
        t = render.ts2phc(cfg)
        self.assertIn("ts2phc.extts_polarity   both", t)
        self.assertIn("ts2phc.pulsewidth       100000000", t)
        self.assertNotIn("refclock PHC", render.chrony_snippet(cfg))


class T5(unittest.TestCase):
    def test_default_pps_pin_sdp1(self):
        from ptpgm import render
        self.assertEqual(config.validate({})["pps"]["pin"], 1)
        self.assertIn("ts2phc.pin_index        1", render.ts2phc(config.validate({})))


class T6(unittest.TestCase):
    def test_chrony_snippet_phc_only_when_enabled(self):
        from ptpgm import render
        cfg = config.validate({"system": {"ntp_allow": "10.0.0.0/8"}})
        off = render.chrony_snippet(cfg, 1, False)
        on = render.chrony_snippet(cfg, 1, True)
        self.assertNotIn("refclock PHC", off)
        self.assertIn("refclock SHM 0 refid NMEA", off)
        self.assertIn("refclock PHC /dev/ptp1 tai poll 0 dpoll -2 refid GPS prefer", on)
        self.assertIn("allow 10.0.0.0/8", on)
        self.assertNotIn("local stratum", on)

    def test_discipline_validation(self):
        with self.assertRaises(config.ValidationError):
            config.validate({"system": {"discipline": "ntpd"}})


class T7(unittest.TestCase):
    def test_ntp_server_allow_rules(self):
        from ptpgm import render
        on = config.validate({})
        self.assertTrue(on["system"]["ntp_server"])
        self.assertIn("allow 10.50.28.0/24", render.chrony_snippet(on, 1, True, ["10.50.28.0/24"]))
        explicit = config.validate({"system": {"ntp_allow": "192.168.0.0/16"}})
        t = render.chrony_snippet(explicit, 1, True, ["10.50.28.0/24"])
        self.assertIn("allow 192.168.0.0/16", t)
        self.assertNotIn("10.50.28.0/24", t)
        off = config.validate({"system": {"ntp_server": False, "ntp_allow": "192.168.0.0/16"}})
        self.assertNotIn("allow", render.chrony_snippet(off, 1, True, ["10.50.28.0/24"]))
        self.assertNotIn("allow", render.chrony_snippet(on, 1, True, []))  # no subnet -> no server
