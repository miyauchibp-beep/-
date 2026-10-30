"""読み取った結果に、機械的な目安（自動チェック）を付ける。

判断するのは Claude と整備士で、ここは見落とし防止の目印。数字の基準は一般的な目安。
"""


def _live(stage):
    return {v["pid"]: v["value"] for v in (stage or {}).get("live") or []}


def _trend(stage, pid):
    for item in ((stage or {}).get("trend") or {}).get("items") or []:
        if item["pid"] == pid:
            return item
    return None


IDLE_MAX_RPM = 1200  # これより高い回転のサンプルは「アイドル」の判定に入れない（ファーストアイドルは 1,100 前後）


def _idle_values(stage, pid):
    """trend の値のうち、回転がアイドル（IDLE_MAX_RPM 以下）だった回のものだけ。

    06 と 0C は毎回同じ周で読むので、並びが対応している（数が違えば全部使う）。
    """
    item, rpm = _trend(stage, pid), _trend(stage, "0C")
    if not item:
        return []
    values = item.get("values") or []
    if not values:
        return []
    if values and rpm and len(rpm.get("values") or []) == len(values):
        return [v for v, r in zip(values, rpm["values"]) if 0 < r <= IDLE_MAX_RPM]
    return values


def _total_trim(stage):
    """短期 + 長期の燃料補正（%）。trend があれば、アイドル中の短期補正の平均を使う。"""
    live = _live(stage)
    idle_short = _idle_values(stage, "06")
    short = round(sum(idle_short) / len(idle_short), 1) if idle_short else live.get("06")
    long_ = live.get("07")
    if short is None or long_ is None:
        return None
    return round(short + long_, 1)


def auto_checks(report):
    flags = []
    stages = report.get("stages") or {}
    idle = stages.get("idle_warm") or stages.get("idle_after_start")
    hold = stages.get("hold_2500rpm")

    ti = _total_trim(idle)
    if ti is not None and abs(ti) > 10:
        flags.append(f"アイドル時の燃料補正の合計（短期＋長期）が {ti:+.1f}% と大きい")
    if ti is not None and hold:
        short = _trend(hold, "06")
        long_ = _live(hold).get("07", _live(idle).get("07"))
        if short and long_ is not None:
            th = round(short["avg"] + long_, 1)
            # エア吸いはアイドルで補正がプラス（薄い）に振れ、回転を上げると戻る。
            # 2026-10-30 ソリオ: アイドル -0.4% → 2,500rpm -8.9%（回転を上げたときだけ濃い側）を
            # 差だけで「エア吸い」と誤って出したので、向きも見る
            if ti > 8 and ti - th > 8:
                flags.append(f"燃料補正の合計がアイドル {ti:+.1f}% → 2,500rpm {th:+.1f}% と大きく下がる"
                             "（低負荷だけ薄い。エア吸いの傾向）")
            elif th < -8 and th - ti < -8:
                purge = _live(hold).get("2E")
                flags.append(f"燃料補正の合計がアイドル {ti:+.1f}% → 2,500rpm {th:+.1f}%（回転を上げたときだけ濃い側。"
                             "パージ・吸入空気量の偏り・燃料過多など"
                             + (f"。2,500rpm でのパージ指令 {purge}%" if purge is not None else "") + "）")
            if abs(ti) > 10 and abs(th) > 10 and ti * th > 0:
                flags.append(f"燃料補正が全域で同じ向きにずれている（アイドル {ti:+.1f}%、2,500rpm {th:+.1f}%）")

    rpm = _idle_values(idle, "0C")
    if len(rpm) >= 10 and max(rpm) - min(rpm) > 200:
        flags.append(f"アイドル回転の上下が大きい（{min(rpm)}〜{max(rpm)} rpm）")

    running_volts = [_live(st).get("42") for st in (idle, stages.get("idle_after_start")) if st]
    running_volts = [v for v in running_volts if v is not None]
    if running_volts:
        if max(running_volts) < 13.2:
            flags.append(f"エンジン稼働中の電圧が低め（{max(running_volts)}V。充電不足の疑い）")
        if max(running_volts) > 15.0:
            flags.append(f"エンジン稼働中の電圧が高め（{max(running_volts)}V）")
    battery = report.get("battery_voltage")
    if battery is not None and not report.get("engine_running") and battery < 12.2:
        flags.append(f"キー ON・エンジン停止時のバッテリー電圧が低め（{battery}V。電装の負荷で少し下がる値）")

    log = stages.get("warmup_log") or []
    if len(log) >= 90:  # 10 秒ごとに 15 分以上
        coolant = [r.get("05") for r in log if r.get("05") is not None]
        if coolant and max(coolant) < 75:
            flags.append(f"15 分以上暖機しても水温が 75℃ に届いていない（最高 {max(coolant)}℃。サーモスタットの疑い）")

    failed = [t for t in report.get("mode06") or [] if not t["passed"]]
    for t in failed[:5]:
        flags.append(f"モード 06 で不合格: {t['name']}（TID {t['tid']}、値 {t['value']}、範囲 {t['min']}〜{t['max']}）")

    free = stages.get("free_log")
    for mark in (free or {}).get("marks") or []:
        changes = [f"{c['name']} {c['before']}→{c['during']}" for c in mark["changes"] if c.get("notable")]
        if changes:
            flags.append(f"症状マーク {mark['no']}（{mark['t']} 秒）の前後で大きく変化: " + "、".join(changes))
    return flags
