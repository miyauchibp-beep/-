"""ELM327 互換アダプタとの通信と、OBD2 応答の解析。"""

import re
import time

PROMPT = b">"

# ATDPN で返るプロトコル番号（先頭の "A" は自動判別の意味）
PROTOCOLS = {
    "1": "SAE J1850 PWM",
    "2": "SAE J1850 VPW",
    "3": "ISO 9141-2",
    "4": "ISO 14230-4 KWP (5 baud init)",
    "5": "ISO 14230-4 KWP (fast init)",
    "6": "ISO 15765-4 CAN (11bit, 500kbps)",
    "7": "ISO 15765-4 CAN (29bit, 500kbps)",
    "8": "ISO 15765-4 CAN (11bit, 250kbps)",
    "9": "ISO 15765-4 CAN (29bit, 250kbps)",
    "A": "SAE J1939 CAN",
}
CAN_PROTOCOLS = {"6", "7", "8", "9"}

# トヨタの古い K-line 車（アレックス NZE121 など）: 宛先 13・9600bps・ヘッダ 81 13 F1。
# Car Scanner の「Toyota OBD-II / EOBD」プロファイルが実際に送っている組み合わせ（2026-10-02 のログで確認）
TOYOTA_KWP = ["ATSH8113F1", "ATIB96", "ATIIA13"]

# 保存済みの設定がないとき・繋がらないときに、順に試す接続設定 (プロトコル, 追加コマンド, 説明)
KNOWN_SETTINGS = [
    ("0", [], "自動判別"),
    ("6", [], "CAN 11bit 500k"),
    ("4", TOYOTA_KWP, "トヨタ K-line（宛先 13・9600bps）"),
]

NO_DATA_MARKERS = ("NO DATA", "?")
ERROR_MARKERS = (
    "UNABLE TO CONNECT",
    "BUS ERROR",
    "CAN ERROR",
    "DATA ERROR",
    "BUFFER FULL",
    "FB ERROR",
    "LV RESET",
    "STOPPED",
)

_FRAME_INDEX = re.compile(r"^([0-9A-F]):\s*(.*)$")
_HEX_ONLY = re.compile(r"^[0-9A-F ]+$")


class ObdError(Exception):
    """アダプタや車両との通信に失敗したとき。"""


def parse_response(raw):
    """ELM327 の生応答を ECU ごとのバイト列リストに変換する。

    ヘッダ表示オフ（ATH0）の前提で、次の形式に対応する。
      - 単一フレーム:   "41 0C 1A F8"
      - 複数ECUの応答:  1 行ずつ別 ECU
      - CAN 複数フレーム: "014" / "0: 49 02 01 31 47 31" / "1: ..." のように
        バイト数の行と、"n:" で始まる連番の行
    データがない（NO DATA 等）ときは空リストを返す。
    """
    text = raw.replace("\x00", "").upper()
    lines = []
    for line in re.split(r"[\r\n]+", text):
        line = line.strip().rstrip(">").strip()
        if not line or line == "SEARCHING...":
            continue
        # K-line の初期化結果。"BUS INIT: ...OK" の後にデータが続く
        if line.startswith("BUS INIT"):
            if "ERROR" in line:
                raise ObdError(line)
            line = line.split("OK", 1)[1].strip() if "OK" in line else ""
            if not line:
                continue
        if any(m in line for m in ERROR_MARKERS):
            raise ObdError(line)
        if line in NO_DATA_MARKERS or "NO DATA" in line:
            continue
        lines.append(line)

    messages = []
    multi = None
    for line in lines:
        m = _FRAME_INDEX.match(line)
        if m:
            if multi is None:
                multi = []
                messages.append(multi)
            multi.extend(_to_bytes(m.group(2)))
            continue
        multi = None
        compact = line.replace(" ", "")
        # CAN 複数フレームの先頭に付くバイト数（例: "014"）は捨てる
        if len(compact) == 3 and _HEX_ONLY.match(compact):
            continue
        if _HEX_ONLY.match(line):
            messages.append(_to_bytes(line))
    return [m for m in messages if m]


def _to_bytes(hex_text):
    compact = hex_text.replace(" ", "")
    if len(compact) % 2:
        compact = compact[:-1]
    return [int(compact[i:i + 2], 16) for i in range(0, len(compact), 2)]


class Elm327:
    """ELM327 互換アダプタ。

    port にはシリアルポート名（例: COM3, /dev/ttyUSB0, /dev/rfcomm0）か、
    read/write を持つオブジェクト（デモ用シミュレータ）を渡す。
    """

    def __init__(self, port, baudrate=38400, timeout=10.0):
        if isinstance(port, str):
            try:
                import serial
            except ImportError as e:
                raise ObdError(
                    "pyserial が入っていません。`pip install pyserial` を実行してください"
                ) from e
            try:
                self._io = serial.Serial(port, baudrate=baudrate, timeout=0.1)
                self._is_serial = True
            except serial.SerialException as e:
                if "PermissionError" in str(e) or "Access is denied" in str(e) or "アクセスが拒否" in str(e):
                    raise ObdError(
                        f"ポート {port} はほかのアプリが使用中です。スマホの Bluetooth をオフにし、"
                        "ほかの黒い画面を閉じて、アダプタを抜き差ししてからやり直してください"
                    ) from e
                if "121" in str(e) or "セマフォ" in str(e) or "semaphore" in str(e).lower():
                    raise ObdError(
                        f"ポート {port} の先のアダプタから Bluetooth の返事がありません。"
                        "アダプタの電源（PWR ランプ）を確認し、ボタンで OFF→ON してからやり直してください。"
                        "直らなければ、Windows で OBDII をいったん削除してペアリングし直し、"
                        "「COM ポート」タブで新しい「発信」の番号を確認してください"
                    ) from e
                raise ObdError(f"ポート {port} を開けません: {e}") from e
        else:
            self._io = port
            self._is_serial = False
        self.timeout = timeout
        self.version = ""
        self.protocol = ""
        self.protocol_name = ""
        self.generic_obd = False
        # 送受信の記録先（command, reply を受け取る関数）。不具合の調査用
        self.log = None

    @property
    def is_can(self):
        return self.protocol in CAN_PROTOCOLS

    def close(self):
        try:
            self.send_raw("ATPC", timeout=1)
        except ObdError:
            pass
        close = getattr(self._io, "close", None)
        if close:
            close()

    def send_raw(self, command, timeout=None):
        """コマンドを送り、プロンプト ">" までの応答文字列を返す。"""
        reset = getattr(self._io, "reset_input_buffer", None)
        if reset:
            reset()
        self._io.write((command + "\r").encode("ascii"))
        if hasattr(self._io, "flush"):
            self._io.flush()
        deadline = time.monotonic() + (timeout or self.timeout)
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self._io.read(64)
            if chunk:
                buf.extend(chunk)
                if PROMPT in chunk:
                    break
        else:
            if self.log:
                self.log(command, "(応答なし) " + buf.decode("ascii", errors="ignore"))
            raise ObdError(f"アダプタから応答がありません（{command}）")
        text = buf.decode("ascii", errors="ignore")
        # エコーが残っていれば取り除く
        if text.lstrip().upper().startswith(command.upper()):
            text = text.lstrip()[len(command):]
        if self.log:
            self.log(command, text)
        return text

    def connect(self, protocol="0", extra_init=(), reset="ATZ"):
        """アダプタを初期化し、車両と通信できる状態にする。

        extra_init には、ATSP の後に送る追加の AT コマンド（例: ["ATKW0"]）を渡す。
        kline_init_test.py で見つかった設定をそのまま使える。
        reset はアダプタのリセットに使うコマンド（ATZ / ATD / ATWS）。
        """
        self.send_raw(reset, timeout=5)
        if self._is_serial:
            time.sleep(0.5)
        for cmd in ("ATE0", "ATL0", "ATS1", "ATH0", "ATAT1"):
            self.send_raw(cmd, timeout=3)
        self.version = self.send_raw("ATI", timeout=3).strip(" \r\n>")
        self.send_raw(f"ATSP{protocol}", timeout=3)
        for cmd in extra_init:
            self.send_raw(cmd, timeout=3)
        # 最初の要求で初期化（プロトコルの自動判別）が行われる。
        # 平成20年ごろより前の国内車は、初期化できても 01 00 に答えない（7F 01 11 等）ことがあるので、
        # 何か返事があれば接続できたとみなす
        raw = self.send_raw("0100", timeout=20)
        messages = parse_response(raw)  # BUS INIT: ...ERROR 等はここで ObdError
        self.generic_obd = any(m[:2] == [0x41, 0x00] for m in messages)
        if not messages and not ("BUS INIT" in raw.upper() and "OK" in raw.upper()):
            raise ObdError(
                "車両から応答がありません。イグニッション ON か、エンジンがかかっているか確認してください"
            )
        self.protocol = self.send_raw("ATDPN", timeout=3).strip(" \r\n>").lstrip("A")
        self.protocol_name = PROTOCOLS.get(self.protocol, f"不明 ({self.protocol})")

    def query(self, mode, pid=None, timeout=None):
        """OBD2 要求を送り、ECU ごとの応答データ（モード/PID 以降）を返す。"""
        cmd = f"{mode:02X}" + (f"{pid:02X}" if pid is not None else "")
        raw = self.send_raw(cmd, timeout=timeout)
        expected = mode + 0x40
        results = []
        for msg in parse_response(raw):
            if not msg or msg[0] != expected:
                continue
            body = msg[1:]
            if pid is not None:
                if not body or body[0] != pid:
                    continue
                body = body[1:]
            results.append(body)
        return results

    def battery_voltage(self):
        text = self.send_raw("ATRV", timeout=3).strip(" \r\n>")
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        return float(m.group(1)) if m else None

