import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")  # run_ai が API の経路を通るように
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

    def test_short_monitor_status(self):
        # 2026-10-12 アレックス NZE121 の実際の応答: "41 01 00"
        st = pids.decode_monitor_status([0x00])
        self.assertEqual((st["mil"], st["dtc_count"], st["readiness_available"]), (False, 0, False))
        st = pids.decode_monitor_status([0x82])
        self.assertEqual((st["mil"], st["dtc_count"]), (True, 2))

    def test_o2_locations(self):
        self.assertEqual(pids.decode(0x13, [0x03]), ("B1S1、B1S2", ""))
        self.assertEqual(pids.decode(0x13, [0x01]), ("B1S1", ""))

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
        self.assertEqual(r["vin"], "*************4567")  # 下 4 桁だけ
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
        self.assertTrue(modes <= {"01", "02", "03", "06", "07", "09", "0A"}, modes)


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


SAMPLE_DIAGNOSIS = {
    "summary": "1 番気筒の失火。点火系から確認する。",
    "candidates": [
        {"title": "イグニッションコイル不良", "likelihood": "高", "system": "点火",
         "evidence": ["P0301 が記録", "フリーズフレームで 1,600rpm・水温 83℃"]},
        {"title": "プラグ摩耗", "likelihood": "中", "system": "点火", "evidence": ["走行距離が多い"]},
    ],
    "data_notes": ["キー ON 時の回転数 0 は正常"],
    "checks": [{"title": "コイルの入れ替え", "how": "1 番と 2 番のコイルを入れ替えて失火が移るか見る",
                "criteria": "失火が 2 番に移ればコイル不良", "tools": "ソケットレンチ"}],
    "repairs": [{"cause": "コイル不良", "work": "コイル交換", "parts": "5,000〜10,000 円", "labor": "0.3h"}],
    "cautions": ["高電圧に注意"],
    "urgency": {"level": "早めに点検", "reason": "失火が続くと触媒を傷める"},
    "customer_explanation": "エンジンの点火に関わる部品の不具合が疑われます。",
}


class _FakeClient:
    """Claude API の代わり。送られた引数を記録し、決まった文を返す（既定は JSON の診断を 2 つに分けて）。"""

    _JSON = json.dumps(SAMPLE_DIAGNOSIS, ensure_ascii=False)

    def __init__(self, chunks=(_JSON[:40], _JSON[40:]), stop_reason="end_turn"):
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
        from obd_scanner.ai_diag import DIAGNOSIS_SCHEMA, diagnose
        client = _FakeClient()
        got = []
        result = diagnose(self.REPORT, "トヨタ bB QNC21", "アイドリング不安定", client=client, on_text=got.append)
        self.assertEqual(result["data"], SAMPLE_DIAGNOSIS)
        self.assertEqual(json.loads("".join(got)), SAMPLE_DIAGNOSIS)  # 進み具合用に断片が届く
        text = result["text"]
        for heading in ("## 見立て", "## 原因候補（可能性の高い順）", "## 確認手順", "## 修理方法",
                        "## 注意点", "## 緊急度", "## お客様向けの説明"):
            self.assertIn(heading, text)        # これまでの診断メモと同じ見出し
        self.assertIn("1. **イグニッションコイル不良**（可能性：高、点火）", text)
        self.assertIn("**早めに点検**", text)
        kw = client.calls[0]
        self.assertEqual(kw["output_config"]["format"], {"type": "json_schema", "schema": DIAGNOSIS_SCHEMA})
        self.assertEqual(kw["model"], "claude-sonnet-5-5")
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
        result = diagnose(self.REPORT, client=_FakeClient(chunks=('{"summary": "途中',), stop_reason="max_tokens"))
        self.assertIsNone(result["data"])            # JSON として読めないときは届いた文をそのまま残す
        self.assertIn("途中で切れました", result["text"])

    def test_schema_is_strict(self):
        # 構造化出力では、すべての object に additionalProperties: false と required が要る
        from obd_scanner.ai_diag import DIAGNOSIS_SCHEMA

        def walk(node):
            if node.get("type") == "object":
                self.assertIs(node["additionalProperties"], False)
                self.assertEqual(set(node["required"]), set(node["properties"]))
                for child in node["properties"].values():
                    walk(child)
            elif node.get("type") == "array":
                walk(node["items"])
        walk(DIAGNOSIS_SCHEMA)

    def test_chat_prompt_stays_markdown(self):
        from obd_scanner.ai_diag import SYSTEM_PROMPT, SYSTEM_PROMPT_JSON
        self.assertIn("出力の形（Markdown）", SYSTEM_PROMPT)
        self.assertIn("JSON スキーマ", SYSTEM_PROMPT_JSON)
        self.assertIn("時計の経過時間ではない", SYSTEM_PROMPT_JSON)  # 前提のルールは両方に入っている

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


class ReplayAdapter:
    """実車の通信記録（scan_logs/raw_*.txt）の応答をそのまま返す疑似アダプタ。

    sequential=True なら、同じコマンドへの応答を記録の順に返す（最後の応答はくり返す）。
    """

    def __init__(self, path, sequential=False):
        self.replies = {}
        self.sequential = sequential
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("> "):
                    cmd, _, reply = line[2:].rstrip("\n").partition(" ")
                    reply = reply.strip().replace(" | ", "\r")
                    if not reply.isascii():
                        reply = "NO DATA"  # 「（車台番号は記録しない）」など、記録していない応答
                    self.replies.setdefault(cmd, []).append(reply)
        self._out = bytearray()

    def write(self, data):
        cmd = data.decode("ascii").strip().upper()
        queue = self.replies.get(cmd)
        if not queue:
            reply = "OK" if cmd.startswith("AT") else "NO DATA"
        elif self.sequential and len(queue) > 1:
            reply = queue.pop(0)
        else:
            reply = queue[0]
        self._out.extend((reply + "\r\r>").encode("ascii"))

    def read(self, size=1):
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk


class RealNze121Test(unittest.TestCase):
    """2026-10-12 アレックス NZE121（エンジン稼働中）の実際の応答で読み取る。"""

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nze121_raw_20261012.txt")
        self.elm = Elm327(ReplayAdapter(path))
        self.elm.connect(protocol="4", extra_init=["ATSH8113F1", "ATIB96", "ATIIA13"])

    def test_full_scan(self):
        from obd_scanner.scan import full_scan
        r = full_scan(self.elm, trend_seconds=0)
        self.assertEqual(r["calibration_id"], "31205100")
        self.assertIsNone(r["vin"])
        self.assertEqual(r["status"]["mil"], False)
        self.assertEqual(r["status"]["dtc_count"], 0)
        self.assertFalse(r["status"]["readiness_available"])
        self.assertEqual(r["dtcs"]["stored"], [])
        self.assertIsNone(r["freeze_frame"])
        live = {v["pid"]: v["value"] for v in r["live"]}
        self.assertEqual(live["0C"], 589)       # 09 34 → 2356 / 4
        self.assertEqual(live["15"], 0.0)       # リア O2 は ECU が 0V と返している
        self.assertEqual(live["14"], 0.055)
        self.assertTrue(r["engine_running"])


class RealNwgnCrankTest(unittest.TestCase):
    """2026-10-29 ホンダ Nワゴン JH1（CAN 29bit、始動不良・プラグかぶり）の実際の応答。"""

    def test_scan_and_cranking(self):
        from unittest import mock
        from obd_scanner.scan import full_scan, record_cranking
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nwgn_jh1_crank_raw_20261029.txt")
        elm = Elm327(ReplayAdapter(path, sequential=True))
        with self.assertRaises(ObdError):
            elm.connect(protocol="6")   # 前回の設定（CAN 11bit）は NO DATA
        elm.connect(protocol="0")
        r = full_scan(elm, trend_seconds=0)
        self.assertEqual(r["vin"], "*******3971")
        self.assertEqual(r["calibration_id"], "37805-5Z1-J760U")
        self.assertFalse(r["engine_running"])
        live = {v["pid"]: v["value"] for v in r["live"]}
        self.assertEqual(live["15"], 1.275)  # 冷えたキー ON でリア O2 は FF（上限）
        # 1 周 0.43 秒（実車と同じ）として進む時計。scan モジュールの time だけを差し替える
        clock = iter(i * 0.43 for i in range(10000))
        fake_time = types.SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda s: None)
        with mock.patch("obd_scanner.scan.time", fake_time):
            crank = record_cranking(elm, {int(p, 16) for p in r["supported_pids"]})
        self.assertEqual(crank["resting_voltage"], 11.9)
        self.assertEqual(crank["min_voltage"], 9.2)
        self.assertEqual(crank["max_rpm"], 475)
        self.assertTrue(crank["rpm_seen"])
        self.assertFalse(crank["started"])


class TwoEcuTest(unittest.TestCase):
    """CAN でエンジンとミッションの 2 つの ECU が答える車（2026-10-29 バネット）。"""

    def test_engine_only_after_connect(self):
        from obd_scanner.scan import full_scan
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False, dtcs=("P0420",), second_ecu=True)
        elm = Elm327(fake)
        elm.connect()
        self.assertTrue(elm.engine_only)
        self.assertIn("ATSH7E0", fake.sent)
        r = full_scan(elm, trend_seconds=0)
        # レディネス・水温はエンジン ECU の値（ミッションの 41 01 00 04 00 00 が混ざらない）
        self.assertTrue(r["status"]["mil"])
        self.assertEqual(r["status"]["dtc_count"], 1)
        live = {v["pid"]: v["value"] for v in r["live"]}
        self.assertEqual(live["05"], 83)
        # 故障コードは全 ECU 宛てで読み、ミッションの P0731 も拾う
        codes = [d["code"] for d in r["dtcs"]["stored"]]
        self.assertEqual(sorted(codes), ["P0420", "P0731"])
        i = fake.sent.index("03")
        self.assertEqual(fake.sent[i - 1], "ATSH7DF")
        self.assertEqual(fake.sent[i + 1], "ATSH7E0")

    def test_freeze_frame_from_transmission(self):
        from obd_scanner.scan import read_freeze_frame
        # エンジンには記録がなく、ミッションに P0731 の記録がある
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False, dtcs=(), second_ecu=True)
        elm = Elm327(fake)
        elm.connect()
        ff = read_freeze_frame(elm)
        self.assertEqual(ff["dtc"], "P0731")
        self.assertIn({"pid": "0D", "name": "車速", "value": 12, "unit": "km/h"}, ff["values"])
        self.assertEqual(fake.sent[-1], "ATSH7E0")  # 読んだあとはエンジン宛てに戻す

    def test_single_ecu_unchanged(self):
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        elm = Elm327(fake)
        elm.connect()
        self.assertFalse(elm.engine_only)
        self.assertFalse(any(c.startswith("ATSH") for c in fake.sent))

    def test_vanette_replay(self):
        from obd_scanner.scan import full_scan
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vanette_skp2_raw_20261029.txt")
        elm = Elm327(ReplayAdapter(path, sequential=True))
        with self.assertRaises(ObdError):
            elm.connect(protocol="7")
        elm.connect(protocol="0")
        r = full_scan(elm, trend_seconds=0)
        self.assertEqual(r["vin"], "********1494")
        sup = r["supported_pids"]
        # 2 つの ECU の対応表を合わせる（以前はエンジンの 43〜4C や A/F センサの読み落としがあった）
        for pid in ("34", "43", "44", "49", "4C"):
            self.assertIn(pid, sup)
        self.assertEqual(r["status"]["dtc_count"], 0)
        self.assertEqual(r["dtcs"]["stored"], [])


class RealDayzTest(unittest.TestCase):
    """2026-10-30 日産 デイズ B21W（三菱 3B20、CAN 11bit、エンジンと CVT の 2 つの ECU）。"""
    DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    def test_replay_scan(self):
        from obd_scanner.scan import full_scan
        elm = Elm327(ReplayAdapter(os.path.join(self.DATA, "dayz_b21w_raw_20261030.txt"), sequential=True))
        elm.connect(protocol="6")
        self.assertTrue(elm.engine_only)     # CVT も答えるのでエンジン宛てに切り替わる
        r = full_scan(elm, trend_seconds=0)
        self.assertEqual(r["calibration_id"], "1860C5800201")
        self.assertEqual(r["dtcs"]["stored"], [])
        m6 = r["mode06"]
        self.assertEqual(len(m6), 8)         # 複数フレーム・1 つの MID に 2 つの TID も読める
        cat = next(x for x in m6 if x["mid"] == "21")
        self.assertEqual((cat["value"], cat["min"], cat["max"], cat["passed"]), (0, 0, 25, True))
        self.assertEqual(sum(1 for x in m6 if x["mid"] == "A2"), 2)

    def test_auto_checks_ignore_rpm_coming_down(self):
        # 暖機後の測定が 2,150rpm から下がる途中で始まった。アイドル回転の上下とは扱わない
        from obd_scanner.checks import auto_checks
        with open(os.path.join(self.DATA, "dayz_b21w_scan_20261030.json"), encoding="utf-8") as f:
            report = json.load(f)
        text = "\n".join(auto_checks(report))
        self.assertNotIn("アイドル回転の上下", text)
        self.assertIn("バッテリー電圧が低め", text)

    def test_wait_for_idle(self):
        from obd_scanner.scan import wait_for_idle
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        fake.rpm = 2150
        elm = Elm327(fake)
        elm.connect()
        n = {"i": 0}

        def sleep(s):
            n["i"] += 1
            if n["i"] == 3:
                fake.rpm = 860          # アクセルを離して回転が下がる
        fake_time = types.SimpleNamespace(monotonic=lambda: n["i"], sleep=sleep)
        from unittest import mock
        with mock.patch("obd_scanner.scan.time", fake_time):
            self.assertEqual(wait_for_idle(elm), 860)
        self.assertGreaterEqual(n["i"], 5)  # 3 回続けてアイドルになるまで待った


class RealSolioTest(unittest.TestCase):
    """2026-10-30 スズキ ソリオ HYBRID MA36S（マイルドハイブリッド、CAN 11bit、ECU 1 つ、較正 ID なし）。"""
    DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    def test_replay_scan(self):
        from obd_scanner.scan import full_scan, is_hybrid
        elm = Elm327(ReplayAdapter(os.path.join(self.DATA, "solio_ma36s_raw_20261030.txt"), sequential=True))
        elm.connect(protocol="6")
        self.assertFalse(elm.engine_only)   # 答える ECU は 1 つだけ
        r = full_scan(elm, trend_seconds=0)
        self.assertIsNone(r["calibration_id"])
        self.assertIsNone(r["vin"])
        self.assertFalse(is_hybrid(r))      # マイルドハイブリッドは汎用 OBD ではガソリン車と同じに見える
        self.assertEqual([x["mid"] for x in r["mode06"]], ["21", "31"])

    def test_rich_at_2500_is_not_vacuum_leak(self):
        from obd_scanner.checks import auto_checks
        with open(os.path.join(self.DATA, "solio_ma36s_scan_20261030.json"), encoding="utf-8") as f:
            report = json.load(f)
        text = "\n".join(auto_checks(report))
        self.assertNotIn("エア吸い", text)
        self.assertIn("回転を上げたときだけ濃い側", text)

    def test_hold_reads_purge_and_maf(self):
        from obd_scanner.scan import hold_rpm_guided
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        fake.rpm = 2500
        elm = Elm327(fake)
        elm.connect()
        from unittest import mock
        with mock.patch("time.sleep"):
            hold = hold_rpm_guided(elm, {0x0C, 0x06, 0x14, 0x07, 0x05}, lambda kind: None,
                                   settle=0, duration=0.05, poll=0)
        self.assertIn("05", {v["pid"] for v in hold["live"]})
        self.assertIn("0107", fake.sent)         # 長期補正も読みに行く
        self.assertNotIn("012E", fake.sent)      # 対応していない項目は読まない


class RealFreedTest(unittest.TestCase):
    """2026-10-30 ホンダ フリード GB3（CAN 29bit、エンジンとミッションの 2 つの ECU）。
    エンジン宛て（ATSHDA10F1）に答えなかったので、全 ECU の応答が混ざったまま読んだ。"""
    DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    def test_readiness_from_engine_ecu(self):
        from obd_scanner.scan import full_scan
        elm = Elm327(ReplayAdapter(os.path.join(self.DATA, "freed_gb3_raw_20261030.txt"), sequential=True))
        with self.assertRaises(ObdError):
            elm.connect(protocol="6")
        elm.connect(protocol="0")
        r = full_scan(elm, trend_seconds=0)
        names = [n for n, _ in r["status"]["monitors"]]
        # ミッションの「41 01 00 04 00 00」（包括部品だけ）ではなく、エンジンの方を使う
        self.assertIn("触媒", names)
        self.assertIn("EGR/VVT", names)
        self.assertEqual(len(r["mode06"]), 15)

    def test_receive_filter_when_physical_header_fails(self):
        from obd_scanner.scan import full_scan
        fake = FakeElm327(vehicle_protocol="7", needs_kw0=False, dtcs=("P0420",), second_ecu=True,
                          physical_header_ok=False)
        elm = Elm327(fake)
        elm.connect()
        self.assertEqual(elm.engine_focus, "filter")
        self.assertIn("ATSHDA10F1", fake.sent)       # まずエンジン宛てを試し
        self.assertIn("ATCRA18DAF110", fake.sent)    # 答えないので受信フィルタに切り替える
        r = full_scan(elm, trend_seconds=0)
        codes = sorted(d["code"] for d in r["dtcs"]["stored"])
        self.assertEqual(codes, ["P0420", "P0731"])  # 故障コードはフィルタを外して全 ECU から
        i = fake.sent.index("03")
        self.assertEqual((fake.sent[i - 1], fake.sent[i + 1]), ("ATAR", "ATCRA18DAF110"))


class AfSensorTest(unittest.TestCase):
    def test_pid34_lambda(self):
        # バネット（マツダ L8）は PID 14 がなく、PID 34（A/F センサ）に対応
        self.assertEqual(pids.decode(0x34, [0x80, 0x00, 0x80, 0x00]), (1.0, ""))
        self.assertEqual(pids.decode(0x34, [0x7A, 0xE1, 0x7F, 0x00])[0], 0.96)
        self.assertEqual(pids.decode(0x30, [12]), (12, "回"))


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

    def test_non_generic_setting_is_tried_last(self):
        # 初期化だけ成功する設定が保存されていても、ほかの設定を先に試し、だめなときだけ使う
        import contextlib
        import io
        from unittest import mock
        import obd_scan
        fake = FakeElm327(vehicle_protocol="4", needs_kw0=False, generic_obd=False)
        elm = Elm327(fake)
        with mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()) as out:
            got = obd_scan.connect_any(elm, ("4", [], "ATZ"))
        self.assertIn("ほかの設定も試します", out.getvalue())
        self.assertIn("CAN 11bit 500k で接続中", out.getvalue())
        self.assertEqual(got[3], "前回つながった設定")
        self.assertFalse(elm.generic_obd)

    def test_no_ai_when_nothing_readable(self):
        import contextlib
        import io
        from unittest import mock
        import obd_scan
        fake = FakeElm327(vehicle_protocol="4", needs_kw0=False, generic_obd=False)
        called = []
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with mock.patch("obd_scanner.simulator.FakeElm327", return_value=fake), \
                        mock.patch.object(obd_scan, "run_ai", lambda *a, **kw: called.append(1)), \
                        mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()) as out:
                    obd_scan.main(["--demo"])
            finally:
                os.chdir(cwd)
        self.assertEqual(called, [])
        self.assertIn("Claude の診断はとばします", out.getvalue())

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


class StagedScanTest(unittest.TestCase):
    """キー ON → 始動 → アイドリング →（暖機）→（2,500rpm）を 1 回で測る流れ。"""

    def _setup(self, coolant=83, step=0):
        from obd_scanner.scan import full_scan
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        fake.engine_running = False
        fake.coolant, fake.coolant_step = coolant, step
        elm = Elm327(fake)
        elm.connect()
        report = full_scan(elm, trend_seconds=0)
        return fake, elm, report

    def _run(self, fake, elm, report, answers):
        import contextlib
        import io
        from unittest import mock
        import obd_scan
        answers = iter(answers)
        with mock.patch.object(obd_scan, "ask_yes", lambda prompt: next(answers)), \
                mock.patch("builtins.input", return_value=""), \
                mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            return obd_scan.run_stages(elm, report, elm.connect, demo_fake=fake)

    def test_engine_off_report_then_idle(self):
        fake, elm, report = self._setup()
        self.assertFalse(report["engine_running"])
        stages = self._run(fake, elm, report, [False])  # 2,500rpm はしない
        self.assertEqual(list(stages), ["idle_after_start"])
        live = {v["pid"]: v["value"] for v in stages["idle_after_start"]["live"]}
        self.assertEqual(live["0C"], 800)
        self.assertIsNotNone(stages["idle_after_start"]["trend"])

    def test_warmup_and_2500(self):
        fake, elm, report = self._setup(coolant=60, step=5)
        stages = self._run(fake, elm, report, [True, True])  # 暖機を待つ・2,500rpm も測る
        self.assertEqual(list(stages), ["idle_after_start", "warmup_log", "idle_warm", "hold_2500rpm"])
        self.assertGreaterEqual(stages["warmup_log"][-1]["05"], 80)

    def test_guided_hold_beeps(self):
        # 回転計のない車でも、音だけで 2,500rpm に合わせられる
        from unittest import mock
        from obd_scanner import scan
        fake, elm, report = self._setup()
        fake.engine_running = True
        supported = {int(p, 16) for p in report["supported_pids"]}
        fake.rpm = 2500
        for seq, first in (([1500, 1500, 2500], "low"), ([3200, 2500], "high")):
            rpms = iter(seq)
            heard = []
            with mock.patch.object(scan, "read_rpm", lambda e: next(rpms, 2500)), mock.patch("time.sleep"):
                hold = scan.hold_rpm_guided(elm, supported, heard.append, settle=0, duration=0.05)
            self.assertEqual(heard, [first, "ok", "start", "done"])
            self.assertEqual(hold["in_range_ratio"], 1.0)

    def test_guided_hold_times_out(self):
        from unittest import mock
        from obd_scanner import scan
        fake, elm, report = self._setup()
        fake.engine_running = True  # 800rpm のまま
        with mock.patch("time.sleep"):
            self.assertIsNone(scan.hold_rpm_guided(elm, set(), lambda k: None, timeout=0.05))

    def test_beep_sound_and_gauge(self):
        import contextlib
        import io
        import wave
        import obd_scan
        for kind, tones in obd_scan._BEEPS.items():
            with wave.open(io.BytesIO(obd_scan._square_wav(tones))) as w:
                self.assertGreater(w.getnframes(), 0, kind)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            obd_scan.rpm_gauge(2500, 2200, 2800)
            obd_scan.rpm_gauge(1500, 2200, 2800)
        self.assertIn("\033[92m", out.getvalue())   # 範囲内は緑
        self.assertIn("もっと踏む", out.getvalue())

    def test_noninteractive_ask_is_no(self):
        import obd_scan
        from unittest import mock
        with mock.patch("sys.stdin", io_stub()):
            self.assertFalse(obd_scan.ask_yes("?"))


def io_stub():
    import io
    return io.StringIO("")


class CrankingTest(unittest.TestCase):
    """エンジンがかからない車: セルを回している間の記録。"""

    def _elm(self):
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        fake.engine_running = False
        elm = Elm327(fake)
        elm.connect()
        return fake, elm

    def test_records_voltage_drop_and_rpm(self):
        from unittest import mock
        from obd_scanner import scan
        fake, elm = self._elm()
        rows = []

        def crank_then_stop(row):
            rows.append(row)
            if len(rows) == 5:  # セルを止める
                fake.voltage, fake.engine_running = 12.3, False
        orig = elm.battery_voltage
        calls = {"n": 0}

        def volt():
            calls["n"] += 1
            if calls["n"] == 2:  # 最初の静止電圧のあとでセルを回し始める
                fake.voltage, fake.engine_running, fake.rpm = 9.6, True, 210
            return orig()
        with mock.patch.object(elm, "battery_voltage", volt), mock.patch("time.sleep"):
            crank = scan.record_cranking(elm, {0x0C, 0x0B}, max_seconds=0.2, on_sample=crank_then_stop)
        self.assertEqual(crank["resting_voltage"], 12.4)
        self.assertEqual(crank["min_voltage"], 9.6)
        self.assertEqual(crank["max_rpm"], 210)
        self.assertTrue(crank["rpm_seen"])
        self.assertFalse(crank["started"])

    def test_no_rpm_seen(self):
        # ECU が回転を認識しない（クランク角センサー系の不良など）でも、電圧の落ち込みで記録できる
        from unittest import mock
        from obd_scanner import scan
        fake, elm = self._elm()
        orig = elm.battery_voltage
        calls = {"n": 0}

        def volt():
            calls["n"] += 1
            if calls["n"] == 2:
                fake.voltage = 10.1
            return orig()
        with mock.patch.object(elm, "battery_voltage", volt), mock.patch("time.sleep"):
            crank = scan.record_cranking(elm, {0x0C, 0x0B}, max_seconds=0.1)
        self.assertFalse(crank["rpm_seen"])
        self.assertEqual(crank["min_voltage"], 10.1)

    def test_no_cranking_times_out(self):
        from unittest import mock
        from obd_scanner import scan
        fake, elm = self._elm()
        with mock.patch("time.sleep"):
            self.assertIsNone(scan.record_cranking(elm, {0x0C}, wait_timeout=0.05))

    def test_prompt_explains_cranking(self):
        from obd_scanner.ai_diag import SYSTEM_PROMPT
        self.assertIn("rpm_seen", SYSTEM_PROMPT)


class PidListTest(unittest.TestCase):
    def test_parse(self):
        import obd_scan
        self.assertEqual(obd_scan.parse_pid_list("15"), [0x15])
        self.assertEqual(obd_scan.parse_pid_list("15, 14,0C"), [0x15, 0x14, 0x0C])
        self.assertEqual(obd_scan.parse_pid_list("O2"), [0x14, 0x15, 0x0C])


class VehicleMemoryTest(unittest.TestCase):
    def test_vehicle_remembered_by_calibration_id(self):
        import obd_scan
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("scan_logs")
                report = {"calibration_id": "31205100", "note": "x"}
                seen = []
                fake_diag = lambda r, v, s, on_text, **kw: seen.append(v) or "ok"
                answers = iter(["", "トヨタ アレックス NZE121", ""])
                with mock.patch.object(obd_scan, "diagnose", fake_diag), \
                        mock.patch.object(obd_scan, "ask", lambda prompt: next(answers)):
                    obd_scan.run_ai(report, "1")
                # 2 回目は車両を空 Enter → 覚えた名前が使われる
                answers = iter(["", "", ""])
                with mock.patch.object(obd_scan, "diagnose", fake_diag), \
                        mock.patch.object(obd_scan, "ask", lambda prompt: next(answers)):
                    obd_scan.run_ai(report, "2")
                self.assertEqual(seen, ["トヨタ アレックス NZE121", "トヨタ アレックス NZE121"])
            finally:
                os.chdir(cwd)


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
                with mock.patch.object(obd_scan, "diagnose", lambda r, v, s, on_text, **kw: seen.append(r) or "ok"):
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

    def test_init_only_setting_is_not_saved(self):
        # 2026-10-30 キャリイ DA63T: 宛先 01 で初期化だけ成功し、何も読めない設定を保存してしまった
        import contextlib
        import io
        from unittest import mock
        fake = FakeElm327(vehicle_protocol="4", needs_kw0=False, init_address="01", silent=True)
        out = io.StringIO()
        with mock.patch("obd_scanner.simulator.FakeElm327", return_value=fake), \
                mock.patch("time.sleep"), contextlib.redirect_stdout(out):
            kline_init_test.main(["--demo"])
        text = out.getvalue()
        self.assertIn("この設定は使わず、次を試します", text)
        self.assertIn("[ 5]", text)                       # 止まらずに次の設定へ進む
        self.assertIn("どの設定でも繋がりませんでした", text)
        self.assertFalse(os.path.exists("working_init.json"))

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


class CasesTest(unittest.TestCase):
    """修理結果の記録（答え合わせ）と、次の診断への受け渡し。"""

    def _setup_logs(self):
        os.makedirs("scan_logs")
        scan = {"calibration_id": "37805-5Z1-J760U", "vin": "JH1-1003971",
                "dtcs": {"stored": [{"code": "P0300", "description": "x"}], "pending": [],
                         "permanent": [{"code": "P0300", "description": "x"}]}}
        with open(os.path.join("scan_logs", "scan_20261029_165618.json"), "w", encoding="utf-8") as f:
            json.dump(scan, f)
        with open(os.path.join("scan_logs", "diag_20261029_165618.md"), "w", encoding="utf-8") as f:
            f.write("# 診断メモ\n\n- 車両: ホンダ Nワゴン JH1\n- 症状: エンジンがかからない\n\n本文\n")
        return scan

    def test_record_and_reuse(self):
        import obd_scan
        import record_result
        from unittest import mock
        from obd_scanner import cases
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                scan = self._setup_logs()
                answers = iter(["", "プラグのかぶり", "プラグ 3 本交換", "5", "2", "プラグの臭い"])
                with mock.patch.object(record_result, "ask", lambda prompt: next(answers)):
                    self.assertEqual(record_result.main(), 0)
                saved = cases.load_cases()
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["cause"], "プラグのかぶり")
                self.assertEqual(saved[0]["vehicle"], "ホンダ Nワゴン JH1")
                self.assertEqual(saved[0]["calibration_id"], "37805-5Z1-J760U")
                self.assertEqual(saved[0]["dtcs"], ["P0300"])
                self.assertIn("候補に入っていた", saved[0]["verdict"])
                with open(os.path.join("scan_logs", "diag_20261029_165618.md"), encoding="utf-8") as f:
                    md = f.read()
                self.assertIn("## 答え合わせ", md)
                self.assertIn("実際の原因: プラグのかぶり", md)
                self.assertTrue(cases.recent_diags()[0]["answered"])

                # 同じ較正 ID の車を診断すると、過去の記録が Claude に渡る
                got = {}
                fake = lambda r, v, s, on_text, past_cases=None: got.update(past=past_cases) or "ok"
                answers = iter(["", "", ""])
                with mock.patch.object(obd_scan, "diagnose", fake), \
                        mock.patch.object(obd_scan, "ask", lambda prompt: next(answers)):
                    obd_scan.run_ai({"calibration_id": scan["calibration_id"]}, "20261030_090000")
                self.assertEqual(got["past"][0]["cause"], "プラグのかぶり")
                # 別の車には渡さない
                self.assertEqual(cases.similar_cases({"calibration_id": "31205100"}), [])
            finally:
                os.chdir(cwd)

    def test_pick_hit_candidate_from_json_diagnosis(self):
        # 決まった形の診断（diag_日時.json）があれば、当たった候補の番号を選ぶ
        import record_result
        from unittest import mock
        from obd_scanner import cases
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self._setup_logs()
                with open(os.path.join("scan_logs", "diag_20261029_165618.json"), "w", encoding="utf-8") as f:
                    json.dump({"diagnosis": SAMPLE_DIAGNOSIS}, f, ensure_ascii=False)
                answers = iter(["", "プラグ摩耗", "プラグ交換", "9", "2", "電極が丸い"])
                out = io.StringIO()
                with mock.patch.object(record_result, "ask", lambda prompt: next(answers)), \
                        contextlib.redirect_stdout(out):
                    self.assertEqual(record_result.main(), 0)
                self.assertIn("1 = イグニッションコイル不良", out.getvalue())
                case = cases.load_cases()[0]
                self.assertEqual((case["hit_rank"], case["hit_candidate"]), (2, "プラグ摩耗"))
                self.assertIn("候補に入っていた", case["verdict"])
                with open(os.path.join("scan_logs", "diag_20261029_165618.md"), encoding="utf-8") as f:
                    self.assertIn("当たった候補: 2 番（プラグ摩耗）", f.read())
                self.assertEqual(cases.verdict_from_rank(0), "3")
                self.assertEqual(cases.verdict_from_rank(1), "1")
            finally:
                os.chdir(cwd)

    def test_run_ai_saves_structured_diagnosis(self):
        import obd_scan
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("scan_logs")
                answers = iter(["", "トヨタ bB", "失火"])
                result = {"data": SAMPLE_DIAGNOSIS, "text": "## 見立て\nx"}
                with mock.patch.object(obd_scan, "diagnose", lambda *a, **kw: result), \
                        mock.patch.object(obd_scan, "ask", lambda prompt: next(answers)), \
                        contextlib.redirect_stdout(io.StringIO()):
                    obd_scan.run_ai({}, "20261030_200000", scan_name="scan_x.json")
                with open(os.path.join("scan_logs", "diag_20261030_200000.json"), encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(saved["diagnosis"]["urgency"]["level"], "早めに点検")
                self.assertEqual(saved["vehicle"], "トヨタ bB")
            finally:
                os.chdir(cwd)

    def test_past_cases_in_message(self):
        from obd_scanner.ai_diag import build_user_message
        msg = build_user_message({"x": 1}, "車", "症状", [{"cause": "プラグのかぶり"}])
        self.assertIn("過去の答え合わせ", msg)
        self.assertIn("プラグのかぶり", msg)
        self.assertNotIn("過去の答え合わせ", build_user_message({"x": 1}))

    def test_diag_memo_names_scan(self):
        import obd_scan
        from unittest import mock
        from obd_scanner import cases
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("scan_logs")
                answers = iter(["", "", ""])
                with mock.patch.object(obd_scan, "diagnose", lambda *a, **kw: "ok"), \
                        mock.patch.object(obd_scan, "ask", lambda prompt: next(answers)):
                    obd_scan.run_ai({}, "20261030_100000", scan_name="scan_20261029_165618.json")
                diag = cases.recent_diags()[0]
                self.assertEqual(diag["scan"], "scan_20261029_165618.json")
                self.assertFalse(diag["answered"])
            finally:
                os.chdir(cwd)


class HybridFreeLogTest(unittest.TestCase):
    """ハイブリッドの判定と、回転数を気にしない記録。"""

    def _elm(self, **kw):
        from obd_scanner.simulator import FakeElm327
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False, dtcs=(), **kw)
        elm = Elm327(fake)
        elm.connect()
        return fake, elm

    def test_hybrid_detected_from_full_scan(self):
        from obd_scanner.scan import full_scan, is_hybrid
        fake, elm = self._elm(hybrid=True)
        fake.engine_running = False
        report = full_scan(elm, trend_seconds=0)
        self.assertIn("5B", report["supported_pids"])
        live = {v["pid"]: v["value"] for v in report["live"]}
        self.assertEqual(live["51"], "ハイブリッド（ガソリン）")
        self.assertEqual(live["5B"], 70.2)
        self.assertTrue(is_hybrid(report))
        fake2, elm2 = self._elm()
        self.assertFalse(is_hybrid(full_scan(elm2, trend_seconds=0)))

    def test_free_log_summarizes_running_part(self):
        from obd_scanner.scan import record_free
        fake, elm = self._elm(hybrid=True)
        fake.engine_running = False
        calls = []

        def on_sample(row):
            calls.append(row)
            if len(calls) == 3:
                fake.engine_running = True  # 途中でエンジンがかかる（ハイブリッドの暖機など）
            if len(calls) == 8:
                raise KeyboardInterrupt

        supported = {0x0C, 0x05, 0x03, 0x06, 0x07, 0x14, 0x15, 0x5B}
        free = record_free(elm, supported, seconds=60, interval=0, on_sample=on_sample)
        self.assertTrue(free["stopped_early"])
        self.assertEqual(len(free["samples"]), 8)
        self.assertEqual(free["engine_running_ratio"], 0.62)
        self.assertEqual(free["while_running"]["rpm"]["max"], 800)
        self.assertIsNotNone(free["while_running"]["front_o2"])
        self.assertEqual(free["hybrid_battery"]["max"], 70.2)
        self.assertEqual(free["voltage"]["min"], 12.4)

    def test_free_log_keeps_going_after_dropout(self):
        from obd_scanner.scan import record_free
        from unittest import mock
        fake, elm = self._elm()
        n = {"i": 0}
        orig = elm.battery_voltage

        def flaky():
            n["i"] += 1
            if n["i"] == 2:
                raise ObdError("CAN ERROR")
            return orig()
        reconnects = []
        with mock.patch.object(elm, "battery_voltage", flaky):
            free = record_free(elm, {0x0C}, seconds=0.05, interval=0.01,
                               reconnect=lambda: reconnects.append(1))
        self.assertTrue(free["samples"][1].get("error"))
        self.assertGreater(len(free["samples"]), 2)
        self.assertEqual(reconnects, [1])

    def test_run_free_demo(self):
        import obd_scan
        fake, elm = self._elm(hybrid=True)
        report = {"supported_pids": ["0C", "05", "14", "15", "5B"]}
        free = obd_scan.run_free(elm, report, lambda: None, demo_fake=fake)
        self.assertGreater(len(free["samples"]), 0)

    def test_prompt_mentions_free_log_and_hybrid(self):
        from obd_scanner.ai_diag import SYSTEM_PROMPT
        self.assertIn("free_log", SYSTEM_PROMPT)
        self.assertIn("READY", SYSTEM_PROMPT)


class PrototypeIdeasTest(unittest.TestCase):
    """スマホで作った試作版から取り入れた機能（モード 06・自動チェック・症状マーク・チャット用の文・CSV）。"""

    def test_mode06_read_on_can(self):
        from obd_scanner.scan import full_scan
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        elm = Elm327(fake)
        elm.connect()
        r = full_scan(elm, trend_seconds=0)
        m6 = {x["mid"]: x for x in r["mode06"]}
        self.assertTrue(m6["01"]["passed"])
        self.assertEqual((m6["21"]["value"], m6["21"]["min"], m6["21"]["max"]), (5, 10, 100))
        self.assertFalse(m6["21"]["passed"])
        self.assertEqual(m6["21"]["name"], "触媒 B1")
        # 符号付きの値（UASID 80 以上）。2026-10-30 Nワゴンで不合格と誤って出た
        self.assertEqual((m6["35"]["value"], m6["35"]["min"], m6["35"]["max"]), (-160, -1000, 1000))
        self.assertTrue(m6["35"]["passed"])

    def test_mode06_skipped_on_kline(self):
        from obd_scanner.scan import read_mode06
        fake = FakeElm327()
        elm = Elm327(fake)
        elm.connect(protocol="4", extra_init=["ATKW0"])
        self.assertIsNone(read_mode06(elm))
        self.assertFalse(any(c.startswith("06") for c in fake.sent))

    def test_auto_checks_vacuum_leak_pattern(self):
        from obd_scanner.checks import auto_checks
        trend = lambda pid, vals: {"pid": pid, "count": len(vals), "min": min(vals), "max": max(vals),
                                   "avg": sum(vals) / len(vals), "values": vals}
        report = {
            "battery_voltage": 11.9, "engine_running": False,
            "mode06": [{"name": "触媒 B1", "tid": "80", "value": 5, "min": 10, "max": 100, "passed": False}],
            "stages": {
                "idle_warm": {"live": [{"pid": "07", "value": 12.0}, {"pid": "42", "value": 14.1}],
                              "trend": {"items": [trend("06", [6.0] * 10), trend("0C", [600] * 5 + [850] * 5)]}},
                "hold_2500rpm": {"trend": {"items": [trend("06", [-8.0] * 10)]}},
            },
        }
        text = "\n".join(auto_checks(report))
        self.assertIn("+18.0%", text)            # アイドルの補正合計
        self.assertIn("エア吸い", text)          # 回転を上げると戻る
        self.assertIn("アイドル回転の上下", text)
        self.assertIn("バッテリー電圧が低め", text)
        self.assertIn("モード 06 で不合格: 触媒 B1", text)
        self.assertNotIn("稼働中の電圧", text)   # 14.1V は正常

    def test_symptom_marks(self):
        from obd_scanner.scan import record_free
        fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        elm = Elm327(fake)
        elm.connect()
        clock = {"t": 0.0}

        def monotonic():
            clock["t"] += 0.05
            return clock["t"]
        presses = {"n": 0}

        def keys():
            presses["n"] += 1
            if presses["n"] == 200:      # 約 10 秒後に症状が出る
                fake.rpm = 450           # 回転が落ち込む
                return [" "]
            if presses["n"] == 330:
                return ["q"]
            return []
        fake_time = types.SimpleNamespace(monotonic=monotonic, sleep=lambda s: None)
        marks = []
        from unittest import mock
        with mock.patch("obd_scanner.scan.time", fake_time):
            free = record_free(elm, {0x0C, 0x06}, seconds=600, interval=2.0, keys=keys, on_mark=marks.append)
        self.assertTrue(free["stopped_early"])
        self.assertEqual(len(free["marks"]), 1)
        self.assertEqual(marks[0]["no"], 1)
        rpm = next(c for c in free["marks"][0]["changes"] if c["pid"] == "0C")
        self.assertEqual(rpm["before"], 800)
        self.assertLess(rpm["during"], 650)
        self.assertTrue(rpm["notable"])

    def test_prompt_only_writes_chat_text(self):
        import obd_scan
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("scan_logs")
                answers = iter(["p", "日産 バネット", "点検"])
                called = []
                with mock.patch.object(obd_scan, "diagnose", lambda *a, **kw: called.append(1)), \
                        mock.patch.object(obd_scan, "ask", lambda prompt: next(answers)):
                    obd_scan.run_ai({"calibration_id": "X", "live": []}, "20261030_120000")
                self.assertEqual(called, [])  # API は使わない
                with open(os.path.join("scan_logs", "prompt_20261030_120000.txt"), encoding="utf-8") as f:
                    text = f.read()
                self.assertIn("お客様向けの説明", text)  # 指示文も入っている
                self.assertIn("日産 バネット", text)
                self.assertTrue(os.path.exists(os.path.join("scan_logs", "diag_20261030_120000.md")))
            finally:
                os.chdir(cwd)

    def test_no_api_key_falls_back_to_prompt(self):
        import obd_scan
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("scan_logs")
                answers = iter(["", "", ""])
                with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), \
                        mock.patch.object(obd_scan, "ask", lambda prompt: next(answers)):
                    obd_scan.run_ai({}, "20261030_130000")
                self.assertTrue(os.path.exists(os.path.join("scan_logs", "prompt_20261030_130000.txt")))
            finally:
                os.chdir(cwd)

    def test_cases_csv_and_hit_rate(self):
        from obd_scanner import cases
        with tempfile.TemporaryDirectory() as tmp:
            items = [{"verdict": cases.VERDICTS["1"], "vehicle": "A", "dtcs": ["P0420"], "recorded_at": "2026-10-30T10:00:00"},
                     {"verdict": cases.VERDICTS["2"], "vehicle": "Nワゴン", "dtcs": []},
                     {"verdict": cases.VERDICTS["3"], "vehicle": "C", "dtcs": []}]
            self.assertEqual(cases.hit_rate(items), (3, 1, 2))
            path = cases.write_csv(items, os.path.join(tmp, "cases.csv"))
            with open(path, "rb") as f:
                data = f.read()
            self.assertTrue(data.startswith(b"\xef\xbb\xbf"))  # Excel で文字化けしない
            text = data.decode("utf-8-sig")
            self.assertIn("実際の原因", text)
            self.assertIn("Nワゴン", text)
            self.assertIn("2026-10-30,", text)


class ZipContentsTest(unittest.TestCase):
    def test_zip_has_every_module(self):
        # 2026-10-30: checks.py を ZIP に入れ忘れ、店の PC で起動できなかった
        import glob
        import make_zip
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = set(make_zip.FILES + make_zip.package_files())
        for path in glob.glob(os.path.join(root, "obd_scanner", "*.py")):
            self.assertIn("obd_scanner/" + os.path.basename(path), files)
        for script in ("obd_scan.py", "record_result.py", "kline_init_test.py"):
            self.assertIn(script, files)


class BatFileTest(unittest.TestCase):
    def test_bat_is_ascii_with_crlf(self):
        # LF 改行や日本語が入ると、cmd.exe がコマンドを読み違える
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("run_kline_test.bat", "run_scan.bat", "run_diag_last.bat", "run_result.bat"):
            with open(os.path.join(root, name), "rb") as f:
                data = f.read()
            data.decode("ascii")
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), name)


if __name__ == "__main__":
    unittest.main()
