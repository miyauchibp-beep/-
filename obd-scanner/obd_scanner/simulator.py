"""実車なしで動作確認するための、ELM327 の簡易シミュレータ。

既定では、平成17年式トヨタ（K-line, プロトコル4）を想定し、
互換アダプタでよくある「キーワード検査で BUS INIT: ...ERROR になる」状態を再現する。
ATKW0（キーワード検査オフ）を送ると初期化に成功する。
"""

import re


class FakeElm327:
    def __init__(self, vehicle_protocol="4", needs_kw0=True, dtcs=("P0301", "P0420"),
                 init_address="33", generic_obd=True, init_baud="10", hybrid=False,
                 second_ecu=False):
        """init_address: ECU が返事をする初期化アドレス（国内独自の車は 33 以外）
        generic_obd: False なら共通規格（01/03）に答えず、KWP の 1A/21 だけ答える"""
        self.vehicle_protocol = vehicle_protocol
        self.needs_kw0 = needs_kw0
        self.init_address = init_address
        self.init_baud = init_baud  # トヨタの古い K-line は 96（9600bps）
        self.generic_obd = generic_obd
        # True ならミッション ECU も答える（CAN で全 ECU 宛てのとき。バネットの再現）
        self.second_ecu = second_ecu
        self.hybrid = hybrid     # True なら燃料の種類（PID 51）とハイブリッドバッテリー残量（PID 5B）に答える
        self.dtcs = list(dtcs)
        self.engine_running = True
        self.rpm = 800           # エンジン稼働中の回転数
        self.voltage = 12.4      # バッテリー電圧（ATRV）
        self.coolant = 83        # 冷却水温（℃）
        self.coolant_step = 0    # 0105 を読むたびに上がる量（暖機のまね）
        self._tick = 0  # O2 センサなどを時間で変化させるためのカウンタ
        self._out = bytearray()
        self.sent = []
        self._reset()

    def _reset(self):
        self.protocol = "0"
        self.keyword_check = True
        self.echo = True
        self.spaces = True
        self.connected = False
        self._just_connected = False
        self.iia = "33"
        self.iib = "10"
        self.header = "C133F1"

    # シリアルポートと同じ read/write を持つ
    def write(self, data):
        cmd = data.decode("ascii").strip().upper().replace(" ", "")
        self.sent.append(cmd)
        reply = self._handle(cmd)
        prefix = cmd + "\r" if self.echo else ""
        self._out.extend((prefix + reply + "\r\r>").encode("ascii"))

    def read(self, size=1):
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    def _handle(self, cmd):
        if cmd.startswith("AT"):
            return self._at(cmd[2:])
        if not re.fullmatch(r"[0-9A-F]+", cmd):
            return "?"
        if not self.connected:
            err = self._init_bus()
            if err:
                return err
        return self._obd(cmd)

    def _at(self, arg):
        if arg in ("Z", "D"):
            self._reset()
            return "\r\rELM327 v1.5" if arg == "Z" else "OK"
        if arg == "I":
            return "ELM327 v1.5"
        if arg == "RV":
            return f"{self.voltage:.1f}V"
        if arg in ("E0", "E1"):
            self.echo = arg == "E1"
        elif arg in ("S0", "S1"):
            self.spaces = arg == "S1"
        elif arg in ("KW0", "KW1"):
            self.keyword_check = arg == "KW1"
        elif arg.startswith("IIA"):
            self.iia = arg[3:]
        elif arg.startswith("IB"):
            self.iib = arg[2:]
        elif arg.startswith("SH"):
            self.header = arg[2:]
        elif arg.startswith("SP"):
            self.protocol = arg[2:].lstrip("A") or "0"
            self.connected = False
        elif arg == "PC":
            self.connected = False
        elif arg == "DPN":
            return self.vehicle_protocol if self.connected else "A0"
        return "OK"

    def _init_bus(self):
        p = self.protocol
        if p not in ("0", self.vehicle_protocol):
            return "BUS INIT: ...ERROR" if p in "345" else "UNABLE TO CONNECT"
        target = self.iia if p in "34" else self.header[2:4]
        if p in "34" and self.iib != self.init_baud:
            return "BUS INIT: ...ERROR"
        if p != "0" and target != self.init_address:
            return "BUS INIT: ...ERROR" if p in "34" else "BUS INIT: ERROR"
        if p == "0" and self.init_address != "33":
            return "SEARCHING...\rUNABLE TO CONNECT"
        if self.needs_kw0 and self.keyword_check:
            return "BUS INIT: ...ERROR" if p != "0" else "SEARCHING...\rUNABLE TO CONNECT"
        self.connected = True
        self._just_connected = True
        return ""

    @property
    def _is_can(self):
        return self.vehicle_protocol in "6789"

    def _obd(self, cmd):
        if self._is_can:
            prefix = "SEARCHING...\r" if self.protocol == "0" else ""
        else:
            prefix = "BUS INIT: ...OK\r" if self.protocol != "0" else "SEARCHING...\r"
        table = {
            "0100": "41 00 BE 3E B8 11",
            "0101": f"41 01 {(0x80 if self.dtcs else 0) | len(self.dtcs):02X} 07 65 00",
            "010C": f"41 0C {self.rpm * 4 >> 8:02X} {self.rpm * 4 & 0xFF:02X}" if self.engine_running else "41 0C 00 00",
            "010D": "41 0D 00",
            "0105": f"41 05 {self.coolant + 40:02X}",
            "0104": "41 04 40",
            "0111": "41 11 26",
            "010F": "41 0F 3C",
            "0120": "41 20 00 00 00 01" if self.hybrid else "41 20 00 00 00 00",
            "0140": "41 40 00 00 80 20" if self.hybrid else "NO DATA",
            "0151": "41 51 11" if self.hybrid else "NO DATA",
            "015B": "41 5B B3" if self.hybrid else "NO DATA",
            "07": "47 00" if self._is_can else "47 00 00 00 00 00 00",
            "0A": "4A 00" if self._is_can else "4A 00 00 00 00 00 00",
            # モード 06（CAN のみ）: MID 01（フロント O2）と 21（触媒 B1）。触媒は下限を下回って不合格
            "0600": "46 00 80 00 00 01" if self._is_can else "NO DATA",
            "0620": "46 20 80 00 00 00" if self._is_can else "NO DATA",
            "0601": "46 01 01 0A 01 2C 00 64 03 E8" if self._is_can else "NO DATA",
            "0621": "46 21 80 01 00 05 00 0A 00 64" if self._is_can else "NO DATA",
            "0902": "014\r0: 49 02 01 4A 54 44\r1: 4B 42 32 30 55 30 30\r2: 31 32 33 34 35 36 37",
            "0904": ("49 04 01 33 31 32 30\r49 04 02 35 31 30 30\r49 04 03 00 00 00 00\r49 04 04 00 00 00 00"
                     if not self._is_can else "013\r0: 49 04 01 34 35 34 31\r1: 30 32 30 30 00 00 00\r2: 00 00 00 00 00 00 00"),
            # フリーズフレーム: P0301 で記録、回転数 1600rpm・水温 83℃
            "020200": "42 02 00 03 01" if self.dtcs else "42 02 00 00 00",
            "020C00": "42 0C 00 19 00",
            "020500": "42 05 00 7B",
        }
        if cmd == "0105":
            self.coolant = min(self.coolant + self.coolant_step, 95)
        if cmd in ("0114", "0115", "0106"):
            self._tick += 1
        # フロント O2 は 0.2V と 0.7V を 2 回ごとに行き来、リア O2 は 0.65V 前後で安定
        front = 0x8C if (self._tick // 2) % 2 else 0x28
        table["0114"] = f"41 14 {front:02X} FF"
        table["0115"] = f"41 15 {0x82 + self._tick % 2:02X} FF"
        table["0106"] = f"41 06 {0x7C + self._tick % 8:02X}"
        if not self.engine_running:
            table["0114"] = "41 14 02 FF"
        if not self.generic_obd:
            reply = {"1A80": "5A 80 38 39 36 36 31", "2101": "61 01 00 7B 0C 80"}.get(cmd)
            if reply is None:
                reply = f"7F {cmd[:2]} 11"  # このサービスには対応していない
        elif cmd == "03":
            body = []
            for code in self.dtcs:
                n = int(code[1:], 16) | ("PCBU".index(code[0]) << 14)
                body += [f"{n >> 8:02X}", f"{n & 0xFF:02X}"]
            if self._is_can:
                # CAN は先頭に件数が付く
                reply = " ".join(["43", f"{len(self.dtcs):02X}"] + body)
            else:
                while len(body) % 6:
                    body.append("00")
                lines = ["43 " + " ".join(body[i:i + 6]) for i in range(0, len(body), 6)]
                reply = "\r".join(lines) if lines else "43 00 00 00 00 00 00"
        else:
            reply = table.get(cmd, "NO DATA")
        if self.second_ecu and self._is_can and self.header not in ("7E0", "DA10F1"):
            # ミッション ECU の応答。バネットの実車と同じく、エンジンより先に返ることがある
            tcm = {"0100": "41 00 98 1C 80 11", "0101": "41 01 00 04 00 00", "0105": "41 05 4C",
                   "03": "43 01 07 31", "07": "47 00",  # ミッションに P0731
                   "020200": "42 02 00 07 31", "020D00": "42 0D 00 0C"}.get(cmd)
            if tcm:
                reply = tcm + "\r" + reply
        text = (prefix if self._just_connected else "") + reply
        self._just_connected = False
        return text if self.spaces else text.replace(" ", "")
