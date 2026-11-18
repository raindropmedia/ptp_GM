import os, socket, sys, threading, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ptpgm import nmea_relay

RMC = b"$GPRMC,174910.00,A,5114.8136,N,00647.4519,E,0.0162,310.100,181126,2.1,E*54"


def with_cs(body):
    return b"$" + body + b"*%02X" % nmea_relay.checksum(body)


class T(unittest.TestCase):
    def test_checksum_of_real_sentence(self):
        self.assertIsNotNone(nmea_relay.parse_rmc_time(with_cs(RMC[1:].split(b"*")[0])))

    def test_shift_rewrites_fraction_and_checksum(self):
        src = with_cs(b"GPRMC,174910.00,A,5114.8136,N,00647.4519,E,0.0162,310.100,181126,2.1,E")
        out = nmea_relay.shift_rmc(src, 130)
        self.assertTrue(out.startswith(b"$GPRMC,174910.130,A,"))
        self.assertEqual(nmea_relay.parse_rmc_time(out), (17 * 3600 + 49 * 60 + 10, 130))
        self.assertTrue(out.endswith(b"*%02X" % nmea_relay.checksum(out[1:].split(b"*")[0])))

    def test_shift_small_delay_is_zero_padded(self):
        src = with_cs(b"GPRMC,000001.00,A,1,N,2,E,0,0,181126,,E")
        self.assertIn(b",000001.030,", nmea_relay.shift_rmc(src, 30))

    def test_rejects_non_rmc_bad_checksum_and_overflow(self):
        self.assertIsNone(nmea_relay.shift_rmc(with_cs(b"GPGGA,174910.00,5114"), 130))
        self.assertIsNone(nmea_relay.shift_rmc(RMC.replace(b"*54", b"*00"), 130))
        src = with_cs(b"GPRMC,174910.90,A,1,N,2,E,0,0,181126,,E")
        self.assertIsNone(nmea_relay.shift_rmc(src, 200))

    def test_relay_forwards_only_shifted_rmc(self):
        rmc = with_cs(b"GPRMC,174910.00,A,1,N,2,E,0,0,181126,,E")
        gga = with_cs(b"GPGGA,174910.00,5114")
        up = socket.socket(); up.bind(("127.0.0.1", 0)); up.listen(1)
        got = []

        def fake():
            conn, _ = up.accept()
            got.append(conn.recv(200))
            conn.sendall(b'{"class":"VERSION"}\n' + gga + b"\r\n" + rmc + b"\r\n" + gga + b"\r\n" + rmc + b"\r\n")
            conn.close()
        threading.Thread(target=fake, daemon=True).start()
        relay = nmea_relay.start_background(("127.0.0.1", 0), up.getsockname(), 100)
        c = socket.create_connection(relay.server_address, timeout=5)
        data = b""
        c.settimeout(2)
        try:
            while True:
                chunk = c.recv(200)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        relay.shutdown()
        self.assertEqual(nmea_relay.parse_rmc_time(data.strip()), (17 * 3600 + 49 * 60 + 10, 100))
        self.assertNotIn(b"GGA", data)
        self.assertIn(b'"nmea":true', got[0])
        self.assertEqual(data.count(b"RMC"), 1)  # duplicate copy dropped

if __name__ == "__main__":
    unittest.main()
