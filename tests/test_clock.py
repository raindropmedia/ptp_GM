import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ptpgm import config, status, system


class Fake:
    """Replaces the bits of ptpgm.system that touch the OS."""
    def __init__(self):
        self.phc = False
        self.cmds = []

    def install(self):
        self.orig = (system.run, system.write_chrony, system.chrony_phc_enabled)
        system.run = lambda cmd, **k: (self.cmds.append(cmd) or (0, "", ""))
        system.chrony_phc_enabled = lambda: self.phc

        def write(cfg, enabled):
            self.phc = enabled
            return True
        system.write_chrony = write

    def restore(self):
        system.run, system.write_chrony, system.chrony_phc_enabled = self.orig


class T(unittest.TestCase):
    def setUp(self):
        self.f = Fake(); self.f.install()
        self.c = status.Collector(lambda: None)
        self.cfg = config.validate({"mode": "grandmaster"})
        self.locked = {"servo_state": "s2", "offset_ns": 5, "freq_ppb": 0, "iface": "x"}

    def tearDown(self):
        self.f.restore()

    def test_phc_enabled_only_after_30s_lock(self):
        self.c._manage_clock(self.cfg, self.locked)
        self.assertFalse(self.f.phc)                    # just locked
        self.c._lock_since -= 31
        self.c._manage_clock(self.cfg, self.locked)
        self.assertTrue(self.f.phc)                     # held 30 s
        self.assertIn(["systemctl", "restart", "chrony.service"], self.f.cmds)

    def test_phc_disabled_after_lock_lost_120s(self):
        self.f.phc = True
        self.c._servo_ok_at -= 0  # never locked in this process -> lost
        self.c._manage_clock(self.cfg, None)
        self.assertFalse(self.f.phc)

    def test_phc_kept_during_short_unlock(self):
        import time
        self.f.phc = True
        self.c._servo_ok_at = time.time() - 10
        self.c._manage_clock(self.cfg, None)
        self.assertTrue(self.f.phc)

    def test_off_mode_never_enables(self):
        off = config.validate({"mode": "off"})
        self.c._lock_since = 1
        self.c._manage_clock(off, self.locked)
        self.assertFalse(self.f.phc)

    def test_phc2sys_discipline_does_not_touch_chrony(self):
        cfg = config.validate({"mode": "grandmaster", "system": {"discipline": "phc2sys"}})
        self.c._manage_clock(cfg, self.locked)
        self.c._lock_since -= 31
        self.c._manage_clock(cfg, self.locked)
        self.assertFalse(self.f.phc)
        self.assertIn(["systemctl", "start", "ptpgm-phc2sys.service"], self.f.cmds)

if __name__ == "__main__":
    unittest.main()
