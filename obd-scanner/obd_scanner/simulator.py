"""実車なしで動作確認するための、ELM327 の簡易シミュレータ。

既定では、平成17年式トヨタ（K-line, プロトコル4）を想定し、
互換アダプタでよくある「キーワード検査で BUS INIT: ...ERROR になる」状態を再現する。
ATKW0（キーワード検査オフ）を送ると初期化に成功する。
"""

import re


class FakeElm327:
    def __init__(self, vehicle_protocol="4", needs_kw0=True, dtcs=("P0301", "P0420"),
                 init_address="33", generic_obd=True):
        """init_address: ECU が返事をする初期化アドレス（国内独自の車は 33 以外）
        generic_obd: False なら共通規格（01/03）に答えず、KWP の 1A/21 だけ答える"""
        self.vehicle_protocol = vehicle_protocol
        self.needs_kw0 = needs_kw0
        self.init_address = init_address
        self.generic_obd = generic_obd
        self.dtcs = list(dtcs)
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
            return "12.4V"
        if arg in ("E0", "E1"):
            self.echo = arg == "E1"
        elif arg in ("S0", "S1"):
            self.spaces = arg == "S1"
        elif arg in ("KW0", "KW1"):
            self.keyword_check = arg == "KW1"
        elif arg.startswith("IIA"):
            self.iia = arg[3:]
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
        if p != "0" and target != self.init_address:
            return "BUS INIT: ...ERROR" if p in "34" else "BUS INIT: ERROR"
        if p == "0" and self.init_address != "33":
            return "SEARCHING...\rUNABLE TO CONNECT"
        if self.needs_kw0 and self.keyword_check:
            return "BUS INIT: ...ERROR" if p != "0" else "SEARCHING...\rUNABLE TO CONNECT"
        self.connected = True
        self._just_connected = True
        return ""

    def _obd(self, cmd):
        prefix = "BUS INIT: ...OK\r" if self.protocol != "0" else "SEARCHING...\r"
        table = {
            "0100": "41 00 BE 3E B8 11",
            "0101": f"41 01 {0x80 | len(self.dtcs):02X} 07 65 00",
            "010C": "41 0C 0C 80",
            "010D": "41 0D 00",
            "0105": "41 05 7B",
        }
        if not self.generic_obd:
            reply = {"1A80": "5A 80 38 39 36 36 31", "2101": "61 01 00 7B 0C 80"}.get(cmd)
            if reply is None:
                reply = f"7F {cmd[:2]} 11"  # このサービスには対応していない
        elif cmd == "03":
            body = []
            for code in self.dtcs:
                n = int(code[1:], 16) | ("PCBU".index(code[0]) << 14)
                body += [f"{n >> 8:02X}", f"{n & 0xFF:02X}"]
            while len(body) % 6:
                body.append("00")
            lines = ["43 " + " ".join(body[i:i + 6]) for i in range(0, len(body), 6)]
            reply = "\r".join(lines) if lines else "43 00 00 00 00 00 00"
        else:
            reply = table.get(cmd, "NO DATA")
        text = (prefix if self._just_connected else "") + reply
        self._just_connected = False
        return text if self.spaces else text.replace(" ", "")
