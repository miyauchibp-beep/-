"""モード 01（現在のデータ）の PID 定義と換算式。SAE J1979 準拠。"""

from collections import namedtuple

Pid = namedtuple("Pid", "pid name unit length decode")


def _a(d):
    return d[0]


def _ab(d):
    return d[0] * 256 + d[1]


def _pct(d):
    return round(d[0] * 100 / 255, 1)


def _trim(d):
    return round(d[0] * 100 / 128 - 100, 1)


def _temp(d):
    return d[0] - 40


def _o2_locations(d):
    """PID 13: 付いている O2 センサ（ビット 0-3 がバンク 1 のセンサ 1-4、4-7 がバンク 2）。"""
    found = [f"B{bit // 4 + 1}S{bit % 4 + 1}" for bit in range(8) if d[0] & (1 << bit)]
    return "、".join(found) if found else "なし"


_FUEL_STATUS = {
    0x00: "-",
    0x01: "開ループ（暖機中）",
    0x02: "閉ループ（O2 フィードバック）",
    0x04: "開ループ（高負荷・減速）",
    0x08: "開ループ（システム異常）",
    0x10: "閉ループ（異常あり）",
}

# PID 51 燃料の種類。0x11〜0x16 がハイブリッド（トヨタなどは 0x01 ガソリンと返す車もある）
_FUEL_TYPE = {
    0x01: "ガソリン", 0x04: "ディーゼル", 0x05: "LPG", 0x08: "電気",
    0x11: "ハイブリッド（ガソリン）", 0x12: "ハイブリッド（エタノール）", 0x13: "ハイブリッド（ディーゼル）",
    0x14: "ハイブリッド（電気）", 0x15: "ハイブリッド（電気とエンジン）", 0x16: "ハイブリッド（回生）",
}
HYBRID_FUEL_TYPES = {v for k, v in _FUEL_TYPE.items() if 0x11 <= k <= 0x16}

PIDS = {
    p.pid: p
    for p in [
        Pid(0x03, "燃料系統の状態", "", 2, lambda d: _FUEL_STATUS.get(d[0], f"0x{d[0]:02X}")),
        Pid(0x04, "エンジン負荷", "%", 1, _pct),
        Pid(0x05, "冷却水温", "°C", 1, _temp),
        Pid(0x06, "短期燃料補正 B1", "%", 1, _trim),
        Pid(0x07, "長期燃料補正 B1", "%", 1, _trim),
        Pid(0x08, "短期燃料補正 B2", "%", 1, _trim),
        Pid(0x09, "長期燃料補正 B2", "%", 1, _trim),
        Pid(0x0A, "燃圧", "kPa", 1, lambda d: d[0] * 3),
        Pid(0x0B, "吸気管圧力", "kPa", 1, _a),
        Pid(0x0C, "エンジン回転数", "rpm", 2, lambda d: round(_ab(d) / 4)),
        Pid(0x0D, "車速", "km/h", 1, _a),
        Pid(0x0E, "点火時期", "°BTDC", 1, lambda d: d[0] / 2 - 64),
        Pid(0x0F, "吸気温", "°C", 1, _temp),
        Pid(0x10, "吸入空気量 (MAF)", "g/s", 2, lambda d: round(_ab(d) / 100, 2)),
        Pid(0x11, "スロットル開度", "%", 1, _pct),
        Pid(0x13, "O2 センサの装着位置", "", 1, _o2_locations),
        Pid(0x14, "O2 センサ B1S1", "V", 2, lambda d: round(d[0] / 200, 3)),
        Pid(0x15, "O2 センサ B1S2", "V", 2, lambda d: round(d[0] / 200, 3)),
        Pid(0x1F, "始動後経過時間", "秒", 2, _ab),
        Pid(0x21, "MIL 点灯後の走行距離", "km", 2, _ab),
        Pid(0x2F, "燃料残量", "%", 1, _pct),
        Pid(0x31, "DTC 消去後の走行距離", "km", 2, _ab),
        Pid(0x33, "大気圧", "kPa", 1, _a),
        Pid(0x3C, "触媒温度 B1S1", "°C", 2, lambda d: round(_ab(d) / 10 - 40, 1)),
        Pid(0x42, "ECU 電圧", "V", 2, lambda d: round(_ab(d) / 1000, 2)),
        Pid(0x43, "絶対負荷", "%", 2, lambda d: round(_ab(d) * 100 / 255, 1)),
        Pid(0x44, "目標空燃比 (λ)", "", 2, lambda d: round(_ab(d) * 2 / 65536, 3)),
        Pid(0x45, "相対スロットル開度", "%", 1, _pct),
        Pid(0x46, "外気温", "°C", 1, _temp),
        Pid(0x49, "アクセル開度 D", "%", 1, _pct),
        Pid(0x4D, "MIL 点灯中の時間", "分", 2, _ab),
        Pid(0x4E, "DTC 消去後の時間", "分", 2, _ab),
        Pid(0x51, "燃料の種類", "", 1, lambda d: _FUEL_TYPE.get(d[0], f"0x{d[0]:02X}")),
        Pid(0x5B, "ハイブリッドバッテリー残量", "%", 1, _pct),
        Pid(0x5C, "エンジン油温", "°C", 1, _temp),
        Pid(0x5E, "燃料消費率", "L/h", 2, lambda d: round(_ab(d) / 20, 2)),
    ]
}

# 一覧表示で優先して出す PID
DEFAULT_LIVE = [0x0C, 0x0D, 0x05, 0x04, 0x11, 0x0F, 0x10, 0x0B, 0x06, 0x07, 0x0E, 0x42]


def decode(pid, data):
    """PID の応答データを (値, 単位) に換算する。未対応・データ不足なら None。"""
    p = PIDS.get(pid)
    if p is None or len(data) < p.length:
        return None
    return p.decode(data), p.unit


def supported_from_bitmap(base, data):
    """PID 00/20/40... の 4 バイトのビットマップから対応 PID 一覧を返す。"""
    supported = []
    for i in range(32):
        byte, bit = divmod(i, 8)
        if byte < len(data) and data[byte] & (0x80 >> bit):
            supported.append(base + i + 1)
    return supported


def decode_monitor_status(data):
    """PID 01（MIL・DTC 数・レディネス）の解析。

    返り値: {"mil": bool, "dtc_count": int, "ignition": "火花点火"/"圧縮着火" または None,
             "monitors": [(名前, 状態), ...], "readiness_available": bool}  状態は "完了" / "未完了"

    本来は 4 バイトだが、古いトヨタ（アレックス NZE121）は 1 バイト（MIL と DTC 数）しか返さない。
    """
    a = data[0]
    if len(data) < 4:
        return {"mil": bool(a & 0x80), "dtc_count": a & 0x7F, "ignition": None,
                "monitors": [], "readiness_available": False}
    b, c, d = data[1:4]
    compression = bool(b & 0x08)
    monitors = []
    common = [("失火", 0), ("燃料系", 1), ("包括部品", 2)]
    for name, bit in common:
        if b & (1 << bit):
            monitors.append((name, "未完了" if b & (0x10 << bit) else "完了"))
    if compression:
        names = ["NMHC 触媒", "NOx/SCR", None, "過給圧", None, "排気センサ", "PM フィルタ", "EGR/VVT"]
    else:
        names = ["触媒", "触媒加熱", "蒸発ガス (EVAP)", "二次空気", "エアコン冷媒", "O2 センサ", "O2 センサヒータ", "EGR/VVT"]
    for bit, name in enumerate(names):
        if name and c & (1 << bit):
            monitors.append((name, "未完了" if d & (1 << bit) else "完了"))
    return {
        "mil": bool(a & 0x80),
        "dtc_count": a & 0x7F,
        "ignition": "圧縮着火（ディーゼル）" if compression else "火花点火（ガソリン）",
        "monitors": monitors,
        "readiness_available": True,
    }
