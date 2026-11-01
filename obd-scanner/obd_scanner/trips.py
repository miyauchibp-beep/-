"""走行中に記録したログを、1 回の運転ごとにまとめ、乗り方のクセを出す。

読めるログ:
- このツールのライブデータ CSV（`--live --csv`。列名は「エンジン回転数(rpm)」など）
- 列名が PID の番号（0C, 0D, 05 …）の CSV。スマホの走行記録係もこの形で書く予定
- Torque などの市販アプリの CSV（よく使う列名だけ対応）
- スキャン結果 JSON の「回転数を気にしない記録」（stages.free_log）

ECU の合計（モード 09）と違い、1 回ごとの中身（何分・何 km・水温が何℃まで・何回転まで）がわかる。
"""

import csv
import json
from datetime import datetime

from . import pids

# 運転の区切り: エンジンが止まって（回転 0 または記録なし）この秒数たったら別の運転
TRIP_GAP_SEC = 60
MAX_STEP_SEC = 10  # 記録の間が空きすぎたときに、距離や時間を水増ししない

# 市販アプリの列名（Torque など）→ PID
ALIASES = {
    "engine rpm": "0C", "rpm": "0C",
    "speed (obd)": "0D", "speed": "0D", "vehicle speed": "0D",
    "engine coolant temperature": "05", "coolant temperature": "05",
    "engine load": "04", "throttle position(manifold)": "11", "throttle position": "11",
    "fuel trim bank 1 short term": "06", "fuel trim bank 1 long term": "07",
    "intake manifold pressure": "0B", "mass air flow rate": "10",
    "voltage (control module)": "42",
}
TIME_COLUMNS = ("時刻", "time", "device time", "gps time", "timestamp", "t")


def _column_pid(name):
    base = name.strip().lstrip("﻿")
    for p in pids.PIDS.values():
        if base == f"{p.name}({p.unit})" or base == p.name:
            return f"{p.pid:02X}"
    if len(base) == 2 and all(c in "0123456789ABCDEFabcdef" for c in base):
        return base.upper()
    lower = base.lower()
    return ALIASES.get(lower) or ALIASES.get(lower.split("(")[0].strip())


def _parse_time(text):
    text = text.strip()
    try:
        return float(text)  # 秒（記録開始からの秒、または UNIX 時刻）
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%d-%b-%Y %H:%M:%S.%f", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def _number(text):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def load_rows(path):
    """ログを読み、時刻（秒）と PID ごとの値の行（新しい順でなく記録順）にそろえる。"""
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        free = (report.get("stages") or {}).get("free_log") or {}
        return [{**{k: v for k, v in s.items() if k != "t"}, "time": s["t"]}
                for s in free.get("samples") or [] if "t" in s]
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        time_col = next((i for i, h in enumerate(header) if h.strip().lower() in TIME_COLUMNS), None)
        cols = {i: _column_pid(h) for i, h in enumerate(header) if i != time_col}
        rows = []
        for n, line in enumerate(reader):
            t = _parse_time(line[time_col]) if time_col is not None and time_col < len(line) else float(n)
            if t is None:
                continue
            row = {"time": t}
            for i, pid in cols.items():
                if pid and i < len(line):
                    value = _number(line[i])
                    if value is not None:
                        row[pid] = value
            rows.append(row)
    return rows


def split_trips(rows):
    """回転が出ている間を 1 回の運転とする。止まって TRIP_GAP_SEC 以上たつか、記録が途切れたら区切る。"""
    trips, current, last_running = [], [], None
    for row in sorted(rows, key=lambda r: r["time"]):
        running = (row.get("0C") or 0) > 0
        if running:
            if current and last_running is not None and row["time"] - last_running > TRIP_GAP_SEC:
                trips.append(current)
                current = []
            current.append(row)
            last_running = row["time"]
        elif current and last_running is not None and row["time"] - last_running > TRIP_GAP_SEC:
            trips.append(current)
            current, last_running = [], None
    if current:
        trips.append(current)
    return [t for t in trips if len(t) >= 2]


def summarize_trip(rows):
    """1 回の運転のまとめ。"""
    total = moving40 = idle = over2500 = over3500 = distance = 0.0
    to70 = None
    start = rows[0]["time"]
    for a, b in zip(rows, rows[1:]):
        dt = min(b["time"] - a["time"], MAX_STEP_SEC)
        if dt <= 0:
            continue
        rpm, speed = a.get("0C") or 0, a.get("0D") or 0
        total += dt
        distance += speed * dt / 3600
        if speed >= 40:
            moving40 += dt
        if speed < 3 and 0 < rpm < 1200:
            idle += dt
        if rpm >= 2500:
            over2500 += dt
        if rpm >= 3500:
            over3500 += dt
        if to70 is None and (a.get("05") or -99) >= 70:
            to70 = a["time"] - start
    coolant = [r["05"] for r in rows if "05" in r]
    rpm = [r["0C"] for r in rows if "0C" in r]
    speed = [r["0D"] for r in rows if "0D" in r]
    pct = lambda x: round(x / total * 100) if total else 0
    trip = {
        "start": start,
        "minutes": round(total / 60, 1),
        "km": round(distance, 1) if speed else None,
        "coolant_start": coolant[0] if coolant else None,
        "coolant_max": max(coolant) if coolant else None,
        "coolant_end": coolant[-1] if coolant else None,
        "minutes_to_70c": round(to70 / 60, 1) if to70 is not None else None,
        "max_rpm": max(rpm) if rpm else None,
        "max_speed": max(speed) if speed else None,
        "idle_pct": pct(idle),
        "over_2500rpm_pct": pct(over2500),
        "over_3500rpm_pct": pct(over3500),
    }
    # OBD の「しっかり走った運転」と同じ条件（10 分以上、時速 40km 以上で 5 分以上、アイドル 30 秒以上）
    trip["good_drive"] = bool(speed) and total >= 600 and moving40 >= 300 and idle >= 30
    trip["stopped_cold"] = bool(coolant) and max(coolant) < 70
    return trip


def habits(trips):
    """運転のまとめを並べて、乗り方のクセを日本語の目安にする。"""
    n = len(trips)
    if not n:
        return {"trips": 0, "notes": ["運転として区切れる記録がありませんでした（回転数の列があるか確認してください）"]}
    cold = sum(1 for t in trips if t["stopped_cold"])
    short = sum(1 for t in trips if t["minutes"] < 10)
    good = sum(1 for t in trips if t["good_drive"])
    minutes = sum(t["minutes"] for t in trips)
    weighted = lambda key: round(sum(t[key] * t["minutes"] for t in trips) / minutes) if minutes else 0
    out = {
        "trips": n,
        "total_minutes": round(minutes, 1),
        "total_km": round(sum(t["km"] or 0 for t in trips), 1),
        "stopped_cold": cold,
        "short_trips": short,
        "good_drives": good,
        "max_rpm": max((t["max_rpm"] or 0) for t in trips),
        "idle_pct": weighted("idle_pct"),
        "over_2500rpm_pct": weighted("over_2500rpm_pct"),
        "over_3500rpm_pct": weighted("over_3500rpm_pct"),
    }
    notes = []
    if cold * 2 >= n:
        notes.append(f"{n} 回中 {cold} 回が、水温 70℃ に届く前に止めている（冷えたままの運転が多い）")
    if short * 2 >= n:
        notes.append(f"{n} 回中 {short} 回が 10 分未満の運転")
    if out["over_3500rpm_pct"] < 1 and out["over_2500rpm_pct"] < 5:
        notes.append(f"回転を上げない運転（2,500 回転以上は走行時間の {out['over_2500rpm_pct']}%、"
                     f"最高 {out['max_rpm']:.0f} 回転）")
    if out["idle_pct"] >= 30:
        notes.append(f"アイドリングの時間が長い（全体の {out['idle_pct']}%）")
    if good * 3 < n:
        notes.append(f"しっかり走った運転（10 分以上・時速 40km 以上で 5 分以上）は {n} 回中 {good} 回")
    out["notes"] = notes or ["目立ったクセはありません"]
    return out


def analyze(paths):
    """ログを読み、運転ごとのまとめと乗り方のクセを返す（Claude に渡す driving_log の形）。"""
    trips = []
    for path in paths:
        trips += [summarize_trip(t) for t in split_trips(load_rows(path))]
    trips.sort(key=lambda t: t["start"])
    return {"trips": trips, "habits": habits(trips)}
