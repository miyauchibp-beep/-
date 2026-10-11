import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kline_init_test
from obd_scanner import dtc, pids
from obd_scanner.elm327 import Elm327, ObdError, parse_response
from obd_scanner.simulator import FakeElm327


class ParseResponseTest(unittest.TestCase):
    def test_single_frame(self):
        self.assertEqual(parse_response("41 0C 1A F8\r\r>"), [[0x41, 0x0C, 0x1A, 0xF8]])

    def test_kline_init_ok_line_is_not_an_error(self):
        self.assertEqual(parse_response("BUS INIT: ...OK\r41 00 BE 3E B8 11\r\r>"),
                         [[0x41, 0x00, 0xBE, 0x3E, 0xB8, 0x11]])

    def test_kline_init_error_raises(self):
        with self.assertRaises(ObdError):
            parse_response("BUS INIT: ...ERROR\r\r>")

    def test_no_data(self):
        self.assertEqual(parse_response("NO DATA\r\r>"), [])

    def test_can_multiframe(self):
        raw = "014\r0: 49 02 01 31 47 31\r1: 4A 43 35 34 34 34 52\r2: 37 32 35 32 33 36 37\r\r>"
        msg = parse_response(raw)[0]
        self.assertEqual(bytes(msg[3:]).decode(), "1G1JC5444R7252367")


class DecodeTest(unittest.TestCase):
    def test_rpm(self):
        self.assertEqual(pids.decode(0x0C, [0x1A, 0xF8]), (1726, "rpm"))

    def test_coolant(self):
        self.assertEqual(pids.decode(0x05, [0x7B]), (83, "°C"))

    def test_supported_bitmap(self):
        self.assertEqual(pids.supported_from_bitmap(0, [0x80, 0, 0, 0x01]), [1, 32])

    def test_dtc_kline_padding(self):
        self.assertEqual(dtc.parse_dtc_response([[0x03, 0x01, 0x04, 0x20, 0, 0]], is_can=False),
                         ["P0301", "P0420"])

    def test_dtc_can_count_byte(self):
        self.assertEqual(dtc.parse_dtc_response([[0x01, 0xC1, 0x00]], is_can=True), ["U0100"])

    def test_describe(self):
        self.assertIn("失火", dtc.describe("P0301"))
        self.assertIn("メーカー独自", dtc.describe("P1349"))


class ConnectTest(unittest.TestCase):
    def test_connect_fails_without_kw0(self):
        with self.assertRaises(ObdError):
            Elm327(FakeElm327()).connect(protocol="4")

    def test_connect_with_extra_init(self):
        elm = Elm327(FakeElm327())
        elm.connect(protocol="4", extra_init=["ATKW0"])
        self.assertEqual(elm.protocol, "4")
        self.assertEqual(elm.query(0x01, 0x0C), [[0x0C, 0x80]])
        codes = dtc.parse_dtc_response(elm.query(0x03), elm.is_can)
        self.assertEqual(codes, ["P0301", "P0420"])


class KlineInitTestScript(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_refuses_snapon_ports(self):
        self.assertEqual(kline_init_test.main(["COM4"]), 2)
        self.assertEqual(kline_init_test.main(["com5"]), 2)

    def test_demo_finds_kw0(self):
        self.assertEqual(kline_init_test.main(["--demo"]), 0)

    def test_only_sends_read_requests(self):
        fake = FakeElm327()
        elm = Elm327(fake)
        log = kline_init_test.Logger(os.path.join(self.tmp.name, "log.txt"))
        for protocol, extra, _ in kline_init_test.VARIANTS:
            kline_init_test.try_variant(elm, log, protocol, extra, wait=0)
        log.close()
        obd_requests = {c for c in fake.sent if not c.startswith("AT")}
        self.assertEqual(obd_requests, {"0100"})


if __name__ == "__main__":
    unittest.main()
