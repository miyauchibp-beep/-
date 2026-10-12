import json
import os
import sys
import tempfile
import types
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


class PortOpenTest(unittest.TestCase):
    def test_port_in_use_message(self):
        # 2026-10-12、スマホがアダプタを掴んだまま COM6 を開いたときの Windows のエラー
        from unittest import mock
        import serial
        err = serial.SerialException(
            "could not open port 'COM6': PermissionError(13, 'アクセスが拒否されました。', None, 5)")
        with mock.patch("serial.Serial", side_effect=err):
            with self.assertRaises(ObdError) as cm:
                Elm327("COM6")
        self.assertIn("ほかのアプリが使用中", str(cm.exception))


class AskPortTest(unittest.TestCase):
    def test_normalize_and_forbid(self):
        from obd_scanner.console import ask_port
        self.assertEqual(ask_port("com7"), "COM7")
        self.assertEqual(ask_port("7"), "COM7")
        self.assertIsNone(ask_port("4"))
        self.assertIsNone(ask_port("com5"))
        self.assertEqual(ask_port("/dev/rfcomm0"), "/dev/rfcomm0")  # Linux のパスは大文字にしない

    def test_remembered_port_is_default(self):
        from unittest import mock
        from obd_scanner import console
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertEqual(console.last_port(), "COM7")
                console.remember_port("COM9")
                with mock.patch("builtins.input", return_value=""), mock.patch("builtins.print"):
                    self.assertEqual(console.ask_port(None), "COM9")
            finally:
                os.chdir(cwd)


class PortTimeoutTest(unittest.TestCase):
    def test_bluetooth_timeout_message(self):
        # 2026-10-12、再起動後に COM7 を開いたときの Windows のエラー
        from unittest import mock
        import serial
        err = serial.SerialException(
            "could not open port 'COM7': OSError(22, 'セマフォがタイムアウトしました。', None, 121)")
        with mock.patch("serial.Serial", side_effect=err):
            with self.assertRaises(ObdError) as cm:
                Elm327("COM7")
        self.assertIn("Bluetooth の返事がありません", str(cm.exception))


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


class ScanTest(unittest.TestCase):
    def _connected(self, **kw):
        elm = Elm327(FakeElm327(vehicle_protocol="6", needs_kw0=False, **kw))
        elm.connect()
        return elm

    def test_full_scan_on_can_car(self):
        from obd_scanner.scan import full_scan
        r = full_scan(self._connected(), trend_seconds=0)
        self.assertEqual(r["vin"], "JTDKB20U001234567")
        self.assertEqual(r["calibration_id"], "45410200")
        self.assertEqual([c["code"] for c in r["dtcs"]["stored"]], ["P0301", "P0420"])
        self.assertEqual(r["dtcs"]["pending"], [])
        self.assertTrue(r["status"]["mil"])
        ff = r["freeze_frame"]
        self.assertEqual(ff["dtc"], "P0301")
        self.assertIn({"pid": "0C", "name": "エンジン回転数", "value": 1600, "unit": "rpm"}, ff["values"])
        self.assertIn("0C", [v["pid"] for v in r["live"]])

    def test_trend_when_engine_running(self):
        from obd_scanner.scan import full_scan
        r = full_scan(self._connected(), trend_seconds=0.2)
        self.assertTrue(r["engine_running"])
        items = {it["pid"]: it for it in r["trend"]["items"]}
        self.assertEqual(set(items), {"14", "15", "06", "0C"})
        front = items["14"]
        self.assertEqual((front["min"], front["max"]), (0.2, 0.7))
        self.assertGreater(front["switches"], 2)
        self.assertNotIn("switches", items["15"])
        self.assertEqual(front["count"], len(front["values"]))

    def test_no_trend_when_engine_off(self):
        from obd_scanner.scan import full_scan
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        fake.engine_running = False
        elm = Elm327(fake)
        elm.connect()
        r = full_scan(elm, trend_seconds=0.2)
        self.assertFalse(r["engine_running"])
        self.assertNotIn("trend", r)

    def test_scan_without_dtcs(self):
        from obd_scanner.scan import full_scan
        r = full_scan(self._connected(dtcs=()), trend_seconds=0)
        self.assertEqual(r["dtcs"]["stored"], [])
        self.assertFalse(r["status"]["mil"])
        self.assertIsNone(r["freeze_frame"])

    def test_non_generic_ecu_is_reported(self):
        from obd_scanner.scan import full_scan
        elm = Elm327(FakeElm327(init_address="10", generic_obd=False))
        elm.connect(protocol="4", extra_init=["ATKW0", "ATIIA10"])
        r = full_scan(elm)
        self.assertFalse(r["generic_obd"])
        self.assertIn("note", r)

    def test_scan_sends_no_clear_or_write(self):
        from obd_scanner.scan import full_scan
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        elm = Elm327(fake)
        elm.connect()
        full_scan(elm)
        modes = {c[:2] for c in fake.sent if not c.startswith("AT")}
        self.assertTrue(modes <= {"01", "02", "03", "07", "09", "0A"}, modes)


class _FakeStream:
    def __init__(self, chunks, stop_reason):
        self.text_stream = iter(chunks)
        self._msg = types.SimpleNamespace(
            stop_reason=stop_reason,
            content=[types.SimpleNamespace(type="thinking", thinking=""),
                     types.SimpleNamespace(type="text", text="".join(chunks))])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


class _FakeClient:
    """Claude API の代わり。送られた引数を記録し、決まった文を返す。"""

    def __init__(self, chunks=("## 見立て\n", "問題なし"), stop_reason="end_turn"):
        self.calls = []
        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return _FakeStream(list(chunks), stop_reason)

        self.beta = types.SimpleNamespace(messages=_Messages())


class AiDiagTest(unittest.TestCase):
    REPORT = {"protocol": "6 ISO 15765-4 CAN", "dtcs": {"stored": [{"code": "P0301", "description": "1番気筒 失火"}]}}

    def test_diagnose_streams_and_returns_text(self):
        from obd_scanner.ai_diag import diagnose
        client = _FakeClient()
        got = []
        text = diagnose(self.REPORT, "トヨタ bB QNC21", "アイドリング不安定", client=client, on_text=got.append)
        self.assertEqual(text, "## 見立て\n問題なし")
        self.assertEqual("".join(got), text)
        kw = client.calls[0]
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["fallbacks"], "default")
        self.assertEqual(kw["betas"], ["server-side-fallback-2026-07-01"])
        self.assertNotIn("thinking", kw)  # Opus 5.5 は thinking を無効にできない
        content = kw["messages"][0]["content"]
        for word in ("トヨタ bB QNC21", "アイドリング不安定", "P0301"):
            self.assertIn(word, content)

    def test_prompt_explains_trend(self):
        from obd_scanner.ai_diag import SYSTEM_PROMPT
        self.assertIn("switches", SYSTEM_PROMPT)

    def test_prompt_explains_operating_time_counters(self):
        # 2026-10-12 の bB で「379分 = 約6時間前に消去」と誤読したため
        from obd_scanner.ai_diag import SYSTEM_PROMPT
        self.assertIn("時計の経過時間ではない", SYSTEM_PROMPT)

    def test_refusal_is_reported(self):
        from obd_scanner.ai_diag import AiDiagError, diagnose
        with self.assertRaises(AiDiagError):
            diagnose(self.REPORT, client=_FakeClient(chunks=(), stop_reason="refusal"))

    def test_truncated_answer_is_marked(self):
        from obd_scanner.ai_diag import diagnose
        text = diagnose(self.REPORT, client=_FakeClient(stop_reason="max_tokens"))
        self.assertIn("途中で切れました", text)

    def test_missing_api_key_message(self):
        from obd_scanner.ai_diag import AiDiagError, diagnose

        class _NoKey:
            class beta:
                class messages:
                    @staticmethod
                    def stream(**kw):
                        raise TypeError("Could not resolve authentication method.")
        with self.assertRaises(AiDiagError) as cm:
            diagnose(self.REPORT, client=_NoKey())
        self.assertIn("ANTHROPIC_API_KEY", str(cm.exception))


class ConnectAnyTest(unittest.TestCase):
    def _toyota(self):
        return FakeElm327(vehicle_protocol="4", needs_kw0=False, init_address="13", init_baud="96")

    def test_finds_toyota_kline_without_saved_settings(self):
        # 2026-10-12、アレックスで run_scan.bat を先に押して UNABLE TO CONNECT になった
        import contextlib
        import io
        from unittest import mock
        import obd_scan
        elm = Elm327(self._toyota())
        with mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()) as out:
            protocol, extra, reset, label = obd_scan.connect_any(elm, None)
        self.assertEqual((protocol, extra, label), ("4", ["ATSH8113F1", "ATIB96", "ATIIA13"],
                                                    "トヨタ K-line（宛先 13・9600bps）"))
        self.assertIn("自動判別 で接続中... だめでした", out.getvalue())

    def test_saved_setting_is_tried_first(self):
        import contextlib
        import io
        import obd_scan
        fake = self._toyota()
        elm = Elm327(fake)
        saved = ("4", ["ATSH8113F1", "ATIB96", "ATIIA13"], "ATZ")
        with contextlib.redirect_stdout(io.StringIO()):
            got = obd_scan.connect_any(elm, saved)
        self.assertEqual(got[3], "前回つながった設定")
        self.assertNotIn("ATSP0", fake.sent)

    def test_stale_saved_setting_falls_back(self):
        # bB で保存した CAN の設定が残ったままアレックスにつないでも、トヨタ K-line に切り替わる
        import contextlib
        import io
        from unittest import mock
        import obd_scan
        elm = Elm327(self._toyota())
        with mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            got = obd_scan.connect_any(elm, ("6", [], "ATZ"))
        self.assertEqual(got[0], "4")

    def test_all_fail_raises(self):
        import contextlib
        import io
        from unittest import mock
        import obd_scan
        elm = Elm327(FakeElm327(vehicle_protocol="4", init_address="99"))
        with mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ObdError):
                obd_scan.connect_any(elm, None)


class FromScanTest(unittest.TestCase):
    def test_latest_scan_is_used(self):
        import obd_scan
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("scan_logs")
                for name, proto in (("scan_20261012_100000.json", "old"), ("scan_20261012_120000.json", "new")):
                    with open(os.path.join("scan_logs", name), "w", encoding="utf-8") as f:
                        json.dump({"protocol": proto, "note": "x"}, f)
                seen = []
                with mock.patch.object(obd_scan, "diagnose", lambda r, v, s, on_text: seen.append(r) or "ok"):
                    self.assertEqual(obd_scan.main(["--from-scan"]), 0)
                self.assertEqual(seen[0]["protocol"], "new")
            finally:
                os.chdir(cwd)

    def test_no_scans_yet(self):
        import obd_scan
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertEqual(obd_scan.main(["--from-scan"]), 1)
            finally:
                os.chdir(cwd)


class KlineInitTestScript(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_auto_detect_records_detected_protocol(self):
        # 2026-10-12 の bB（CAN）: 自動判別で成功したら、0 ではなく見つかった番号を保存する
        import contextlib
        import io
        from unittest import mock
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        out = io.StringIO()
        with mock.patch("obd_scanner.simulator.FakeElm327", return_value=fake), contextlib.redirect_stdout(out):
            self.assertEqual(kline_init_test.main(["--demo", "--round", "1", "--only", "10"]), 0)
        self.assertIn("→ プロトコル 6 ISO 15765-4 CAN", out.getvalue())

    def test_default_run_connects_can_car_first(self):
        # bB（CAN）でダブルクリック起動しても、最初の自動判別で成功して止まる
        import contextlib
        import io
        from unittest import mock
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        out = io.StringIO()
        with mock.patch("obd_scanner.simulator.FakeElm327", return_value=fake), contextlib.redirect_stdout(out):
            self.assertEqual(kline_init_test.main(["--demo"]), 0)
        self.assertIn("[ 1] 自動判別", out.getvalue())
        self.assertNotIn("[ 2]", out.getvalue())

    def test_can_direct_is_tried_when_auto_detect_fails(self):
        # 自動判別で CAN を見つけられない互換品でも、CAN 直接指定（2 番目）で繋がる
        import contextlib
        import io
        from unittest import mock
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        orig = fake._init_bus
        fake._init_bus = lambda: "SEARCHING...\rUNABLE TO CONNECT" if fake.protocol == "0" else orig()
        out = io.StringIO()
        with mock.patch("obd_scanner.simulator.FakeElm327", return_value=fake), contextlib.redirect_stdout(out):
            self.assertEqual(kline_init_test.main(["--demo"]), 0)
        text = out.getvalue()
        self.assertIn("[ 1] 自動判別（新しい車はここで繋がる） ... 失敗", text)
        self.assertIn("[ 2] CAN 11bit 500k を直接指定 ... 成功", text)
        self.assertIn("→ プロトコル 6", text)

    def test_round3_only_uses_can(self):
        self.assertTrue(all(p in "6789" for p, _, _ in kline_init_test.ROUND3))

    def test_round4_uses_other_resets(self):
        fake = FakeElm327()
        elm = Elm327(fake)
        log = kline_init_test.Logger(os.path.join(self.tmp.name, "log.txt"))
        protocol, extra, _, reset = kline_init_test.ROUND4[0]
        kline_init_test.try_variant(elm, log, protocol, extra, wait=0, reset=reset)
        log.close()
        self.assertIn("ATD", fake.sent)
        self.assertNotIn("ATZ", fake.sent)

    def test_scan_uses_saved_reset(self):
        import obd_scan
        with open("working_init.json", "w", encoding="utf-8") as f:
            json.dump({"protocol": "4", "extra_init": ["ATKW0"], "reset": "ATD"}, f)
        self.assertEqual(obd_scan.load_init_settings(), ("4", ["ATKW0"], "ATD"))
        fake = FakeElm327()
        elm = Elm327(fake)
        elm.connect(protocol="4", extra_init=["ATKW0"], reset="ATD")
        self.assertEqual(fake.sent[0], "ATD")
        self.assertNotIn("ATZ", fake.sent)

    def test_round4_runs_from_main_without_atz(self):
        from unittest import mock
        fake = FakeElm327()
        with mock.patch("obd_scanner.simulator.FakeElm327", return_value=fake):
            self.assertEqual(kline_init_test.main(["--demo", "--round", "4"]), 0)
        self.assertNotIn("ATZ", fake.sent)

    def _toyota_kline(self):
        # アレックス NZE121: 宛先 13・9600bps でだけ返事をする（Car Scanner のログより）
        return FakeElm327(vehicle_protocol="4", needs_kw0=False, init_address="13", init_baud="96")

    def test_toyota_kline_found_in_default_round(self):
        import contextlib
        import io
        from unittest import mock
        fake = self._toyota_kline()
        out = io.StringIO()
        with mock.patch("obd_scanner.simulator.FakeElm327", return_value=fake), contextlib.redirect_stdout(out):
            self.assertEqual(kline_init_test.main(["--demo"]), 0)
        text = out.getvalue()
        self.assertIn("[ 3] トヨタ K-line（宛先 13・9600bps） ... 成功", text)
        self.assertIn("ATSH8113F1 ATIB96 ATIIA13", text)

    def test_generic_settings_fail_on_toyota_kline(self):
        # 2026-10-12 の実車と同じく、宛先 33・10400bps の設定はすべて失敗する
        fake = self._toyota_kline()
        elm = Elm327(fake)
        log = kline_init_test.Logger(os.path.join(self.tmp.name, "log.txt"))
        results = [kline_init_test.try_variant(elm, log, p, extra, wait=0)
                   for p, extra, _ in kline_init_test.ROUND1[:-1]]
        log.close()
        self.assertTrue(all(r is None for r in results))

    def test_scan_on_toyota_kline(self):
        from obd_scanner.scan import full_scan
        elm = Elm327(self._toyota_kline())
        elm.connect(protocol="4", extra_init=kline_init_test.TOYOTA_KWP)
        self.assertTrue(elm.generic_obd)
        r = full_scan(elm, trend_seconds=0)
        self.assertEqual(r["calibration_id"], "31205100")
        self.assertEqual([c["code"] for c in r["dtcs"]["stored"]], ["P0301", "P0420"])

    def test_refuses_snapon_ports(self):
        self.assertEqual(kline_init_test.main(["COM4"]), 2)
        self.assertEqual(kline_init_test.main(["com5"]), 2)

    def test_demo_finds_kw0(self):
        self.assertEqual(kline_init_test.main(["--demo", "--round", "1"]), 0)

    def _run(self, fake, round_no):
        elm = Elm327(fake)
        log = kline_init_test.Logger(os.path.join(self.tmp.name, "log.txt"))
        results = [(extra, kline_init_test.try_variant(elm, log, p, extra, wait=0))
                   for p, extra, *_ in kline_init_test.ROUNDS[round_no]]
        return elm, log, results

    def test_round2_finds_non_standard_init_address(self):
        # 2026-10-12 の実車ログと同じく、宛先 33 には返事がない ECU
        fake = FakeElm327(init_address="10", generic_obd=False)
        _, _, results = self._run(fake, 1)
        self.assertTrue(all(r is None for _, r in results))
        elm, log, results = self._run(fake, 2)
        self.assertEqual([(e, r) for e, r in results if r], [(["ATKW0", "ATIIA10"], "init")])
        self.assertEqual(kline_init_test.ROUND2[0][0], "0")  # 自動判別が先頭
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
            for protocol, extra, _, *rest in variants:
                kline_init_test.try_variant(elm, log, protocol, extra, wait=0, reset=rest[0] if rest else "ATZ")
        kline_init_test.run_probes(elm, log)
        log.close()
        obd_requests = {c for c in fake.sent if not c.startswith("AT")}
        # 読み取り系（01, 03, 1A, 21）だけ。消去(04)や書き込み系は送らない
        self.assertTrue(obd_requests <= set(kline_init_test.PROBES))
        self.assertFalse({c[:2] for c in obd_requests} & {"04", "14", "2E", "3B", "31", "10", "11"})


class BatFileTest(unittest.TestCase):
    def test_bat_is_ascii_with_crlf(self):
        # LF 改行や日本語が入ると、cmd.exe がコマンドを読み違える
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("run_kline_test.bat", "run_scan.bat", "run_diag_last.bat"):
            with open(os.path.join(root, name), "rb") as f:
                data = f.read()
            data.decode("ascii")
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), name)


if __name__ == "__main__":
    unittest.main()
