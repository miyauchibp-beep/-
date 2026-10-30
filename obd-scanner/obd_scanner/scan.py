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
        # 複数の ECU が答えたときは、どれかが対応していれば読む
        found = sorted({p for body in bodies for p in pids.supported_from_bitmap(base, body)})
        supported.extend(p for p in found if p not in supported)
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
            with elm.all_ecus():
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
    """フリーズフレーム（故障が記録された瞬間のデータ）を読む。記録がなければ None。

    エンジン ECU だけに問い合わせている車で記録がなければ、全 ECU 宛てでも読む
    （ミッションのコード、例: バネットの P0731 の記録はミッション側にあることがある）。
    """
    ff = _read_freeze_frame(elm)
    if ff is None and elm.engine_only:
        with elm.all_ecus():
            ff = _read_freeze_frame(elm)
    return ff


def _read_freeze_frame(elm):
    try:
        causes = _freeze_query(elm, 0x02)
    except ObdError:
        return None
    # 複数の ECU が答えたときは、記録がある（00 00 でない）ものを使う
    cause = next((c for c in causes if len(c) >= 2 and c[:2] != [0, 0]), None)
    if cause is None:
        return None
    code = dtc.decode_pair(cause[0], cause[1])
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
TREND_PIDS = [0x14, 0x34, 0x15, 0x06, 0x0C]  # 0x34 は A/F センサ（電圧型 O2 の代わりに付く車）
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
    report["mode06"] = read_mode06(elm)
    if report["engine_running"] and trend_seconds > 0:
        report["trend"] = sample_trend(elm, supported, trend_seconds)
    return report


# ---- モード 06: ECU が自分で行った検査（O2 センサ・触媒・失火など）の結果 ----

# よく出る MID（検査の対象）の名前。SAE J1979 の表から
MODE06_NAMES = {
    0x01: "O2 センサ B1S1", 0x02: "O2 センサ B1S2", 0x03: "O2 センサ B1S3",
    0x05: "O2 センサ B2S1", 0x06: "O2 センサ B2S2",
    0x21: "触媒 B1", 0x22: "触媒 B2",
    0x31: "EGR", 0x35: "VVT B1", 0x36: "VVT B2",
    0x39: "EVAP（0.150 インチ）", 0x3A: "EVAP（0.090 インチ）", 0x3B: "EVAP（0.040 インチ）", 0x3C: "EVAP（0.020 インチ）",
    0x41: "O2 センサヒータ B1S1", 0x42: "O2 センサヒータ B1S2", 0x45: "O2 センサヒータ B2S1",
    0x61: "二次空気", 0x81: "燃料系 B1", 0x82: "燃料系 B2",
    0xA1: "失火（全体）", **{0xA1 + n: f"失火 {n} 番気筒" for n in range(1, 13)},
}


def read_mode06(elm, max_mids=40):
    """モード 06 の検査結果を読む（CAN の車だけ）。読めなければ None。

    1 件 = MID・TID（検査の番号）・UASID（単位の種類）・測定値・下限・上限。
    単位の換算はしないが、下限〜上限に入っているか（passed）は単位によらず判定できる。
    """
    if not elm.is_can:
        return None  # K-line などは形式が違う（古い方式）ので読まない
    mids = []
    base = 0x00
    while base <= 0xE0:
        try:
            bodies = elm.query(0x06, base)
        except ObdError:
            break
        if not bodies:
            break
        found = sorted({m for body in bodies for m in pids.supported_from_bitmap(base, body)})
        mids.extend(m for m in found if m % 0x20)
        if base + 0x20 not in found:
            break
        base += 0x20
    if not mids:
        return None
    results = []
    for mid in mids[:max_mids]:
        try:
            bodies = elm.query(0x06, mid)
        except ObdError:
            continue
        for body in bodies:
            data = [mid] + body
            for i in range(0, len(data) - 8, 9):
                rec = data[i:i + 9]
                if rec[0] != mid:
                    break
                value, low, high = (rec[3] << 8 | rec[4]), (rec[5] << 8 | rec[6]), (rec[7] << 8 | rec[8])
                results.append({
                    "mid": f"{mid:02X}", "name": MODE06_NAMES.get(mid, f"MID {mid:02X}"),
                    "tid": f"{rec[1]:02X}", "uasid": f"{rec[2]:02X}",
                    "value": value, "min": low, "max": high, "passed": low <= value <= high,
                })
    return results or None


# ---- エンジン始動をはさんだ段階ごとの測定 ----

WARMUP_PIDS = [0x05, 0x0C, 0x06, 0x07, 0x14, 0x34, 0x15]


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
        with elm.all_ecus():
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
                    timeout=120.0, poll=0.3, on_rpm=None):
    """回転数を目標付近に保ってもらい、保てたら自動で測る（PC を見なくてよいよう、音で案内する）。

    beep(kind) の kind: "low"（もっと踏む）, "high"（踏みすぎ）, "ok"（範囲内）, "start"（記録開始）, "done"（終了）
    on_rpm(rpm, low, high) を渡すと、読むたびに呼ぶ（回転ゲージの表示用）。
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
        if on_rpm and rpm is not None:
            on_rpm(rpm, low, high)
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
                if on_rpm:
                    on_rpm(v["value"], low, high)
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


# ---- 回転数を気にせず記録する（ハイブリッド・メンテナンスモードなど） ----

FREE_PIDS = [0x0C, 0x05, 0x03, 0x06, 0x07, 0x14, 0x34, 0x15, 0x04, 0x0D, 0x5B, 0x42]


def is_hybrid(report):
    """燃料の種類（PID 51）か、ハイブリッドバッテリー残量（PID 5B）の対応から判定する。"""
    if "5B" in (report.get("supported_pids") or []):
        return True
    return any(v["pid"] == "51" and v["value"] in pids.HYBRID_FUEL_TYPES for v in report.get("live") or [])


# 症状マークの前後で比べる項目と、「大きく変わった」とみなす差
MARK_PIDS = {"0C": ("エンジン回転数", 150), "0D": ("車速", 5), "04": ("エンジン負荷", 15),
             "06": ("短期燃料補正", 5), "07": ("長期燃料補正", 5), "14": ("O2 センサ B1S1", 0.3),
             "34": ("A/F センサ λ", 0.05), "15": ("O2 センサ B1S2", 0.3), "voltage": ("電圧", 0.5),
             "05": ("冷却水温", 5)}


def _mark_changes(samples, t):
    """マークの直前（-10〜-2 秒）と発生時（-2〜+4 秒）の平均を比べる。"""
    before = [s for s in samples if t - 10 <= s["t"] < t - 2]
    during = [s for s in samples if t - 2 <= s["t"] <= t + 4]
    changes = []
    for key, (name, notable) in MARK_PIDS.items():
        b = [s[key] for s in before if isinstance(s.get(key), (int, float))]
        d = [s[key] for s in during if isinstance(s.get(key), (int, float))]
        if not b or not d:
            continue
        avg_b, avg_d = round(sum(b) / len(b), 3), round(sum(d) / len(d), 3)
        changes.append({"pid": key, "name": name, "before": avg_b, "during": avg_d,
                        "during_min": min(d), "during_max": max(d),
                        "notable": abs(avg_d - avg_b) >= notable})
    return changes


def record_free(elm, supported, seconds=300.0, interval=2.0, on_sample=None, reconnect=None, max_samples=None,
                keys=None, on_mark=None):
    """回転数の目標なしで、決めた時間だけ一定間隔で記録する（Ctrl+C で早めに終われる）。
    通信が切れたサンプルは error として残し、reconnect があれば繋ぎ直して続ける。
    keys を渡すと、待ち時間の間に呼び、押されたキーの一覧（" " = 症状マーク、"q" = 終了）を受け取る。

    ハイブリッドは READY でもエンジンが止まったり、メンテナンスモードでも回転が固定だったりするので、
    回転数で区切らず、エンジンがかかっていた間の O2・燃料補正・水温の動きをまとめる。
    """
    pid_list = [p for p in FREE_PIDS if p in supported]
    samples = []
    marks = []
    start = time.monotonic()
    interrupted = False
    try:
        while time.monotonic() - start < seconds and (max_samples is None or len(samples) < max_samples):
            t = time.monotonic()
            row = {"t": round(t - start, 1)}
            try:
                row["voltage"] = elm.battery_voltage()
                for v in read_live(elm, pid_list):
                    row[v["pid"]] = v["value"]
            except ObdError:
                # 通信が切れても、それまでの記録は残して続ける
                row["error"] = True
                if reconnect:
                    try:
                        reconnect()
                    except ObdError:
                        pass
            samples.append(row)
            if on_sample:
                on_sample(row)
            if _wait_keys(keys, t + interval, start, marks, on_mark):
                interrupted = True
                break
    except KeyboardInterrupt:
        interrupted = True
    running = [s for s in samples if (s.get("0C") or 0) > 0]

    def span(rows, key):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if not vals:
            return None
        return {"min": min(vals), "max": max(vals), "avg": round(sum(vals) / len(vals), 3),
                "first": vals[0], "last": vals[-1]}

    front = [r["14"] for r in running if "14" in r]
    sides = [v > O2_RICH_LEAN_THRESHOLD for v in front]
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "seconds": samples[-1]["t"] if samples else 0,
        "interval_sec": interval,
        "stopped_early": interrupted,
        "engine_running_ratio": round(len(running) / len(samples), 2) if samples else None,
        "closed_loop_seen": any(str(r.get("03", "")).startswith("閉ループ") for r in samples),
        "coolant": span(samples, "05"),
        "voltage": span(samples, "voltage"),
        "hybrid_battery": span(samples, "5B"),
        "while_running": {
            "rpm": span(running, "0C"),
            "front_o2": span(running, "14"),
            "front_o2_switches": sum(1 for a, b in zip(sides, sides[1:]) if a != b),
            "front_af_lambda": span(running, "34"),
            "rear_o2": span(running, "15"),
            "short_trim": span(running, "06"),
            "long_trim": span(running, "07"),
        },
        "marks": [dict(m, changes=_mark_changes(samples, m["t"])) for m in marks],
        "samples": samples,
        "pending_dtcs": read_pending(elm),
    }


def _wait_keys(keys, until, start, marks, on_mark):
    """次の読み取りまで待つ。その間に押されたキーを見る。"q" なら True。"""
    while True:
        for key in (keys() if keys else []):
            if key == "q":
                return True
            mark = {"no": len(marks) + 1, "t": round(time.monotonic() - start, 1)}
            marks.append(mark)
            if on_mark:
                on_mark(mark)
        left = until - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.1, left) if keys else left)


# ---- エンジンがかからない車: クランキング中の記録 ----

def record_cranking(elm, supported, wait_timeout=60.0, max_seconds=20.0, poll=0.0, on_sample=None):
    """セルを回している間の電圧・回転数・吸気管圧力を記録する。

    電圧はアダプタが測るので、ECU が回転を認識していなくてもクランキングを検知できる
    （静止時より 1V 以上下がったら開始）。回転が 0 のまま電圧が戻って 3 秒たったら終わり。
    回転が 500rpm 以上で 3 秒続いたら「始動した」として終わる。
    セルが回されないまま wait_timeout 秒たったら None。
    """
    rest = elm.battery_voltage()
    if rest is None:
        return None
    pid_list = [p for p in (0x0C, 0x0B) if p in supported]
    samples = []
    t0 = None
    start_wait = time.monotonic()
    quiet_since = running_since = None
    while True:
        now = time.monotonic()
        try:
            volt = elm.battery_voltage()
            values = {v["pid"]: v["value"] for v in read_live(elm, pid_list)}
        except ObdError:
            volt, values = None, {}  # 電圧が下がりすぎてアダプタが一瞬止まることがある
        rpm = values.get("0C")
        cranking = (volt is not None and volt < rest - 1.0) or (rpm or 0) > 0
        if t0 is None:
            if not cranking:
                if now - start_wait > wait_timeout:
                    return None
                time.sleep(poll)
                continue
            t0 = now
        row = {"t": round(now - t0, 2), "voltage": volt, "rpm": rpm, "map_kpa": values.get("0B")}
        samples.append(row)
        if on_sample:
            on_sample(row)
        running_since = (running_since or now) if (rpm or 0) >= 500 else None
        quiet_since = (quiet_since or now) if not cranking else None
        started = running_since is not None and now - running_since >= 3
        if started or (quiet_since and now - quiet_since >= 3) or now - t0 >= max_seconds:
            break
        time.sleep(poll)
    crank = [s for s in samples if s["voltage"] is not None]
    rpms = [s["rpm"] for s in samples if s["rpm"] is not None]
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "resting_voltage": rest,
        "min_voltage": min((s["voltage"] for s in crank), default=None),
        "max_rpm": max(rpms, default=None),
        "rpm_seen": any(r > 0 for r in rpms),
        "started": started,
        "seconds": samples[-1]["t"] if samples else 0,
        "samples": samples,
        "pending_dtcs": read_pending(elm),
    }
