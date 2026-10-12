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
        self.assertEqual(kline_init_test.main(["--demo", "--round", "1"]), 0)

    def _run(self, fake, round_no):
        elm = Elm327(fake)
        log = kline_init_test.Logger(os.path.join(self.tmp.name, "log.txt"))
        results = [(extra, kline_init_test.try_variant(elm, log, p, extra, wait=0))
                   for p, extra, _ in kline_init_test.ROUNDS[round_no]]
        return elm, log, results

    def test_round2_finds_non_standard_init_address(self):
        # 2026-10-12 の実車ログと同じく、宛先 33 には返事がない ECU
        fake = FakeElm327(init_address="10", generic_obd=False)
        _, _, results = self._run(fake, 1)
        self.assertTrue(all(r is None for _, r in results))
        elm, log, results = self._run(fake, 2)
        self.assertEqual([(e, r) for e, r in results if r], [(["ATKW0", "ATIIA10"], "init")])
        elm.connect(protocol="4", extra_init=["ATKW0", "ATIIA10"])
        answered = dict(kline_init_test.run_probes(elm, log))
        log.close()
        self.assertEqual(sorted(answered), ["0100", "0101", "03", "1A80", "1A9B", "2101"])
        self.assertEqual(answered["2101"], "61 01 00 7B 0C 80")
        self.assertEqual(answered["0100"], "7F 01 11")

    def test_only_sends_read_requests(self):
        fake = FakeElm327()
        elm = Elm327(fake)
        log = kline_init_test.Logger(os.path.join(self.tmp.name, "log.txt"))
        for variants in kline_init_test.ROUNDS.values():
            for protocol, extra, _ in variants:
                kline_init_test.try_variant(elm, log, protocol, extra, wait=0)
        kline_init_test.run_probes(elm, log)
        log.close()
        obd_requests = {c for c in fake.sent if not c.startswith("AT")}
        # 読み取り系（01, 03, 1A, 21）だけ。消去(04)や書き込み系は送らない
        self.assertTrue(obd_requests <= set(kline_init_test.PROBES))
        self.assertFalse({c[:2] for c in obd_requests} & {"04", "14", "2E", "3B", "31", "10", "11"})


class BatFileTest(unittest.TestCase):
    def test_bat_is_ascii_with_crlf(self):
        # LF 改行や日本語が入ると、cmd.exe がコマンドを読み違える
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_kline_test.bat")
        data = open(path, "rb").read()
        data.decode("ascii")
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))


if __name__ == "__main__":
    unittest.main()
