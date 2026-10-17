"""接続済みの Elm327 から、診断に必要なデータを一通り読み取る（読み取りのみ）。"""

import time
from datetime import datetime

from . import dtc, pids
from .elm327 import ObdError, parse_response


def supported_pids(elm):
    """PID 00/20/40/... を順に問い合わせ、対応 PID の一覧を返す。"""
    supported = []
    base = 0x00
    while base <= 0xC0:
        try:
            bodies = elm.query(0x01, base)
        except ObdError:
            break
        if not bodies:
            break
        found = pids.supported_from_bitmap(base, bodies[0])
        supported.extend(found)
        if base + 0x20 not in found:
            break
        base += 0x20
    return supported


def _freeze_query(elm, pid, frame=0):
    """モード 02（フリーズフレーム）。応答は 42 PID フレーム番号 データ。"""
    raw = elm.send_raw(f"02{pid:02X}{frame:02X}")
    results = []
    for msg in parse_response(raw):
        if len(msg) >= 3 and msg[0] == 0x42 and msg[1] == pid and msg[2] == frame:
            results.append(msg[3:])
    return results


def read_vin(elm):
    """モード 09 PID 02 で車台番号を読む。読めなければ None。

    国内の車は車台番号が 17 桁でない（例: QNC21-1234567）ことも多く、対応していない車もある。
    """
    return read_mode09_text(elm, 0x02)


def read_calibration_id(elm):
    """モード 09 PID 04 で ECU の較正 ID（ソフトの版）を読む。例: アレックス NZE121 は 31205100。"""
    return read_mode09_text(elm, 0x04)


def read_mode09_text(elm, pid):
    """モード 09 の文字列（車台番号・較正 ID など）を読む。"""
    try:
        bodies = elm.query(0x09, pid)
    except ObdError:
        return None
    if not bodies:
        return None
    if len(bodies) == 1:
        data = bodies[0][1:]  # CAN: 先頭はデータ項目数
    else:
        # K-line: 1 行ごとに 連番 + 4 バイト
        data = [b for body in sorted(bodies, key=lambda x: x[0]) for b in body[1:]]
    text = "".join(chr(b) for b in data if 0x20 < b < 0x7F)
    return text or None


def read_dtcs(elm):
    """現在・保留中・消せない（永久）故障コードを読む。"""
    result = {}
    for mode, key in ((0x03, "stored"), (0x07, "pending"), (0x0A, "permanent")):
        try:
            codes = dtc.parse_dtc_response(elm.query(mode), elm.is_can)
        except ObdError:
            codes = []
        result[key] = [{"code": c, "description": dtc.describe(c)} for c in codes]
    return result


def read_live(elm, pid_list):
    """指定 PID の現在値を 1 回ずつ読む。"""
    values = []
    for pid in pid_list:
        p = pids.PIDS.get(pid)
        if p is None:
            continue
        try:
            bodies = elm.query(0x01, pid)
        except ObdError:
            continue
        if not bodies:
            continue
        decoded = pids.decode(pid, bodies[0])
        if decoded is not None:
            values.append({"pid": f"{pid:02X}", "name": p.name, "value": decoded[0], "unit": decoded[1]})
    return values


def read_freeze_frame(elm):
    """フリーズフレーム（故障が記録された瞬間のデータ）を読む。記録がなければ None。"""
    try:
        cause = _freeze_query(elm, 0x02)
    except ObdError:
        return None
    if not cause or cause[0][:2] in ([0, 0], []):
        return None
    code = dtc.decode_pair(cause[0][0], cause[0][1]) if len(cause[0]) >= 2 else None
    values = []
    for pid in pids.DEFAULT_LIVE + [0x03, 0x05, 0x0D]:
        p = pids.PIDS.get(pid)
        if p is None or any(v["pid"] == f"{pid:02X}" for v in values):
            continue
        try:
            bodies = _freeze_query(elm, pid)
        except ObdError:
            continue
        if bodies:
            decoded = pids.decode(pid, bodies[0])
            if decoded is not None:
                values.append({"pid": f"{pid:02X}", "name": p.name, "value": decoded[0], "unit": decoded[1]})
    return {"dtc": code, "description": dtc.describe(code) if code else "", "values": values}


# エンジン稼働中に続けて読む項目（O2 センサの反転・触媒の働き・燃料補正のばらつきを見る）
TREND_PIDS = [0x14, 0x15, 0x06, 0x0C]
O2_RICH_LEAN_THRESHOLD = 0.45  # V。これをまたいだら「反転」と数える


def sample_trend(elm, supported, seconds=10.0, max_samples=200, on_round=None):
    """数秒間くり返し読み、項目ごとの最小・最大・平均と、フロント O2 の反転回数をまとめる。

    1 回の値では O2 センサが反転しているか判断できないため。
    Bluetooth 経由だと 1 周 0.3〜0.5 秒かかるので、速い反転は数え漏れることがある。
    """
    pid_list = [p for p in TREND_PIDS if p in supported]
    if not pid_list:
        return None
    series = {p: [] for p in pid_list}
    # フロント O2 は速く動くので、ほかの項目の合間に毎回はさんで読む
    order = pid_list
    if 0x14 in pid_list:
        others = [p for p in pid_list if p != 0x14]
        order = [0x14] + [x for p in others for x in (p, 0x14)]
    start = time.monotonic()
    rounds = 0
    while time.monotonic() - start < seconds and rounds < max_samples:
        values = read_live(elm, order)
        for v in values:
            series[int(v["pid"], 16)].append(v["value"])
        rounds += 1
        if on_round:
            on_round(values)
    elapsed = time.monotonic() - start
    items = []
    for pid, values in series.items():
        if not values:
            continue
        p = pids.PIDS[pid]
        item = {
            "pid": f"{pid:02X}", "name": p.name, "unit": p.unit, "count": len(values),
            "interval_sec": round(elapsed / len(values), 2),
            "min": min(values), "max": max(values), "avg": round(sum(values) / len(values), 3),
            "values": values,
        }
        if pid == 0x14:
            sides = [v > O2_RICH_LEAN_THRESHOLD for v in values]
            item["switches"] = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
        items.append(item)
    return {
        "seconds": round(elapsed, 1),
        "rounds": rounds,
        "items": items,
    }


def engine_running(live):
    return any(v["pid"] == "0C" and v["value"] > 0 for v in live)


def full_scan(elm, trend_seconds=10.0):
    """診断に使うデータを一通り読み、辞書にまとめる。

    エンジンがかかっていれば、trend_seconds 秒間続けて読んだ傾向（trend）も加える。
    """
    report = {
        "adapter": elm.version,
        "protocol": f"{elm.protocol} {elm.protocol_name}",
        "generic_obd": elm.generic_obd,
        "battery_voltage": elm.battery_voltage(),
    }
    if not elm.generic_obd:
        report["note"] = "共通規格（01 00）に答えない ECU のため、読み取れる項目が限られます"
        return report
    supported = supported_pids(elm)
    report["supported_pids"] = [f"{p:02X}" for p in supported]
    report["vin"] = read_vin(elm)
    report["calibration_id"] = read_calibration_id(elm)
    status = elm.query(0x01, 0x01) if 0x01 in supported else []
    if status and status[0]:
        report["status"] = pids.decode_monitor_status(status[0])
    report["dtcs"] = read_dtcs(elm)
    # PID 02（記録の原因 DTC）はモード 02 専用で、モード 01 の対応一覧には出ないので常に試す
    report["freeze_frame"] = read_freeze_frame(elm)
    live_pids = [p for p in pids.PIDS if p in supported]
    report["live"] = read_live(elm, live_pids)
    report["engine_running"] = engine_running(report["live"])
    if report["engine_running"] and trend_seconds > 0:
        report["trend"] = sample_trend(elm, supported, trend_seconds)
    return report


# ---- エンジン始動をはさんだ段階ごとの測定 ----

WARMUP_PIDS = [0x05, 0x0C, 0x06, 0x07, 0x14, 0x15]


def read_rpm(elm):
    """エンジン回転数。読めなければ None。"""
    try:
        bodies = elm.query(0x01, 0x0C)
    except ObdError:
        return None
    decoded = pids.decode(0x0C, bodies[0]) if bodies else None
    return decoded[0] if decoded else None


def read_coolant(elm):
    try:
        bodies = elm.query(0x01, 0x05)
    except ObdError:
        return None
    decoded = pids.decode(0x05, bodies[0]) if bodies else None
    return decoded[0] if decoded else None


def read_pending(elm):
    try:
        codes = dtc.parse_dtc_response(elm.query(0x07), elm.is_can)
    except ObdError:
        codes = []
    return [{"code": c, "description": dtc.describe(c)} for c in codes]


def measure_running(elm, supported, trend_seconds=20.0):
    """エンジン稼働中の 1 段階分: ライブデータ全部・数秒間の動き・保留中の故障コード。"""
    live_pids = [p for p in pids.PIDS if p in supported]
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "live": read_live(elm, live_pids),
        "trend": sample_trend(elm, supported, trend_seconds) if trend_seconds > 0 else None,
        "pending_dtcs": read_pending(elm),
    }


def warmup_sample(elm, supported):
    """暖機中の記録 1 回分（水温・回転・燃料補正・O2）。"""
    row = {"time": datetime.now().isoformat(timespec="seconds")}
    for v in read_live(elm, [p for p in WARMUP_PIDS if p in supported]):
        row[v["pid"]] = v["value"]
    return row


def hold_rpm_guided(elm, supported, beep, target=2500, band=300, settle=3.0, duration=15.0,
                    timeout=120.0, poll=0.3):
    """回転数を目標付近に保ってもらい、保てたら自動で測る（PC を見なくてよいよう、音で案内する）。

    beep(kind) の kind: "low"（もっと踏む）, "high"（踏みすぎ）, "ok"（範囲内）, "start"（記録開始）, "done"（終了）
    目標に入らないまま timeout 秒たったら None。
    """
    low, high = target - band, target + band
    start = time.monotonic()
    in_range_since = None
    last_beep = 0.0
    while True:
        if time.monotonic() - start > timeout:
            return None
        rpm = read_rpm(elm)
        now = time.monotonic()
        if rpm is not None and low <= rpm <= high:
            if in_range_since is None:
                in_range_since = now
                beep("ok")
            if now - in_range_since >= settle:
                break
        else:
            in_range_since = None
            if rpm is not None and rpm > 0 and now - last_beep >= 1.0:
                beep("low" if rpm < low else "high")
                last_beep = now
        time.sleep(poll)

    beep("start")
    rpms = []

    def guide(values):
        for v in values:
            if v["pid"] == "0C":
                rpms.append(v["value"])
                if not low <= v["value"] <= high:
                    beep("low" if v["value"] < low else "high")

    trend = sample_trend(elm, supported, duration, on_round=guide)
    beep("done")
    in_range = sum(1 for r in rpms if low <= r <= high)
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "target_rpm": target,
        "rpm_band": [low, high],
        "in_range_ratio": round(in_range / len(rpms), 2) if rpms else None,
        "live": [],
        "trend": trend,
        "pending_dtcs": read_pending(elm),
    }
