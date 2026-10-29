r"""車の故障コード・フリーズフレーム・ライブデータを読み取り、Claude に診断させる（車へは読み取りのみ）。

使い方（Windows）:
    python obd_scan.py COM7            # 読み取って保存 → 車両情報・症状を聞いて Claude が診断
    python obd_scan.py COM7 --no-ai    # 読み取りと保存だけ
    python obd_scan.py COM7 --live     # ライブデータを表示し続ける（Ctrl+C で終了）
    python obd_scan.py COM7 --live --pids o2 --csv   # O2 センサ 2 つと回転数だけを速く表示・記録
    python obd_scan.py --from-scan scan_logs\scan_20261012_121847.json   # 保存済みの結果で診断し直す
    python obd_scan.py --from-scan     # ファイル名を省くと、いちばん新しい読み取り結果を使う
    python obd_scan.py --demo          # アダプタなしで動作確認

Claude の診断には、環境変数 ANTHROPIC_API_KEY に API キーを設定しておく必要があります。

kline_init_test.py が作った working_init.json があれば、その接続設定を使います。
故障コードの消去や書き込みはしません。
"""

import argparse
import csv
import glob
import json
import os
import sys
import time
from datetime import datetime

try:
    from obd_scanner import pids
    from obd_scanner.ai_diag import AiDiagError, diagnose
    from obd_scanner.console import ask_port, remember_port, run_from_console
    from obd_scanner.elm327 import KNOWN_SETTINGS, Elm327, ObdError
    from obd_scanner.scan import (full_scan, hold_rpm_guided, measure_running, read_coolant, read_live,
                                  read_rpm, record_cranking, supported_pids, warmup_sample)
except ImportError:
    print("obd_scanner フォルダが見つかりません。obd_scan.py と同じ場所にフォルダごとコピーしてください。")
    if os.name == "nt":
        input("\nEnter キーを押すと閉じます...")
    sys.exit(1)


INIT_FILE = "working_init.json"


def load_init_settings(path=INIT_FILE):
    """前回つながった接続設定（kline_init_test.py か obd_scan.py が保存）。なければ None。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("protocol", "0"), data.get("extra_init", []), data.get("reset", "ATZ")
    except (OSError, ValueError):
        return None


def save_init_settings(protocol, extra, reset, label, path=INIT_FILE):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"protocol": protocol, "extra_init": extra, "reset": reset, "label": label,
                       "found_at": datetime.now().isoformat(timespec="seconds")},
                      f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def connect_any(elm, saved):
    """保存済みの設定 → 既知の設定（自動判別・CAN・トヨタ K-line）の順に、繋がるまで試す。

    繋がった設定を返す。全部だめなら ObdError（最後のエラー）。
    """
    candidates = []
    if saved:
        candidates.append((*saved, "前回つながった設定"))
    for protocol, extra, label in KNOWN_SETTINGS:
        if not saved or (protocol, list(extra)) != (saved[0], list(saved[1])):
            candidates.append((protocol, list(extra), "ATZ", label))
    last_error = None
    for i, (protocol, extra, reset, label) in enumerate(candidates):
        if i and protocol in "345":
            time.sleep(3)  # K-line は失敗した直後の初期化を受け付けないことがある
        print(f"  {label} で接続中... ", end="", flush=True)
        try:
            elm.connect(protocol=protocol, extra_init=extra, reset=reset)
        except ObdError as e:
            print(f"だめでした（{e}）")
            last_error = e
            continue
        print("OK")
        return elm.protocol or protocol, extra, reset, label
    raise last_error


def print_report(r):
    print(f"\n通信方式: {r['protocol']}   バッテリー電圧: {r.get('battery_voltage')} V")
    if r.get("note"):
        print(r["note"])
        return
    if r.get("vin"):
        print(f"車台番号: {r['vin']}")
    if r.get("calibration_id"):
        print(f"較正 ID: {r['calibration_id']}")
    s = r.get("status")
    if s:
        line = f"警告灯(MIL): {'点灯' if s['mil'] else '消灯'}"
        if s.get("ignition"):
            line += f"   エンジン: {s['ignition']}"
        print(line)
        if s.get("readiness_available", True):
            pending = [n for n, st in s["monitors"] if st == "未完了"]
            print(f"レディネス: 未完了 {len(pending)} 項目" + (f"（{'、'.join(pending)}）" if pending else ""))
        else:
            print("レディネス: この車の ECU は返しません")

    labels = {"stored": "故障コード（現在）", "pending": "故障コード（保留中）", "permanent": "故障コード（消去不可）"}
    for key, label in labels.items():
        codes = r["dtcs"][key]
        print(f"\n■ {label}: {len(codes)} 件")
        for c in codes:
            print(f"   {c['code']}  {c['description']}")

    ff = r.get("freeze_frame")
    if ff:
        print(f"\n■ フリーズフレーム（{ff['dtc']} 記録時）")
        for v in ff["values"]:
            print(f"   {v['name']:<16} {v['value']} {v['unit']}")

    print("\n■ ライブデータ")
    for v in r["live"]:
        print(f"   {v['name']:<16} {v['value']} {v['unit']}")

    t = r.get("trend")
    if t:
        print(f"\n■ {t['seconds']} 秒間の動き")
        for it in t["items"]:
            line = f"   {it['name']:<16} 最小 {it['min']} ／ 最大 {it['max']} ／ 平均 {it['avg']} {it['unit']}"
            if "switches" in it:
                line += f"  （0.45V をまたいだ回数: {it['switches']} 回、約 {it['interval_sec']} 秒ごとに読み取り）"
            print(line)
    elif r.get("engine_running") is False:
        print("\n（エンジン停止中のため、O2 センサなどの動きは見ていません）")


def parse_pid_list(text):
    """"15,14,0C" や "o2" を PID の一覧にする。"""
    if text.strip().lower() == "o2":
        return [0x14, 0x15, 0x0C]
    return [int(x, 16) for x in text.replace(" ", "").split(",") if x]


def live_loop(elm, csv_path=None, interval=0.5, only=None):
    supported = set(supported_pids(elm))
    pid_list = [p for p in (only or pids.DEFAULT_LIVE) if p in supported and p in pids.PIDS]
    if not pid_list:
        print("ライブデータに対応した項目がありません。")
        return
    writer = f = None
    if csv_path:
        f = open(csv_path, "w", newline="", encoding="utf-8-sig")  # Excel で文字化けしないよう BOM 付き
        writer = csv.writer(f)
        writer.writerow(["時刻"] + [f"{pids.PIDS[p].name}({pids.PIDS[p].unit})" for p in pid_list])
    print("ライブデータ（Ctrl+C で終了）")
    try:
        while True:
            values = read_live(elm, pid_list)
            if os.name == "nt":
                os.system("cls")
            else:
                print("\033[H\033[J", end="")
            print(f"ライブデータ {datetime.now():%H:%M:%S}（Ctrl+C で終了）\n")
            for v in values:
                print(f"  {v['name']:<16} {v['value']:>8} {v['unit']}")
            if writer:
                by_pid = {v["pid"]: v["value"] for v in values}
                writer.writerow([datetime.now().isoformat(timespec="milliseconds")]
                                + [by_pid.get(f"{p:02X}", "") for p in pid_list])
                f.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n終了しました。")
    finally:
        if f:
            f.close()
            print(f"記録: {csv_path}")


def ask(prompt):
    """対話できるときだけ聞く（テストやリダイレクト時は空文字）。"""
    if not sys.stdin or not sys.stdin.isatty():
        return ""
    return input(prompt).strip()


VEHICLES_FILE = "vehicles.json"


def load_vehicles():
    try:
        with open(VEHICLES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def remember_vehicle(key, vehicle):
    """較正 ID ごとに、入力された車両名を覚える（次回の既定値にする）。"""
    if not key or not vehicle:
        return
    vehicles = load_vehicles()
    vehicles[key] = vehicle
    try:
        with open(VEHICLES_FILE, "w", encoding="utf-8") as f:
            json.dump(vehicles, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def run_ai(report, stamp, report_path=None):
    """車両情報・症状を聞いて Claude に診断させ、Markdown で保存する。"""
    print("\n" + "=" * 50)
    if ask("Claude に診断させますか？ [Enter = はい / n = いいえ]: ").lower() in ("n", "no", "いいえ"):
        return
    key = report.get("calibration_id") or report.get("vin")
    known = load_vehicles().get(key, "") if key else ""
    if known:
        vehicle = ask(f"車両（Enter で「{known}」）: ") or known
    else:
        vehicle = ask("車両（例: トヨタ bB QNC21 H28年 85000km）: ")
    remember_vehicle(key, vehicle)
    symptom = ask("症状・お客様の申告（例: アイドリングが不安定、警告灯が点いた）: ")
    print("\nClaude が診断しています...\n")
    try:
        text = diagnose(report, vehicle, symptom, on_text=lambda t: print(t, end="", flush=True))
    except AiDiagError as e:
        print(f"診断できませんでした: {e}")
        return
    print()
    md_path = os.path.join("scan_logs", f"diag_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 診断メモ {datetime.now():%Y-%m-%d %H:%M}\n\n")
        f.write(f"- 車両: {vehicle or '（未入力）'}\n- 症状: {symptom or '（未入力）'}\n\n")
        f.write(text + "\n\n---\nClaude による候補です。最終判断は実車で確認してください。\n")
    if report_path:
        report["ai_diagnosis"] = {"vehicle": vehicle, "symptom": symptom, "text": text}
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n診断メモを保存しました: {md_path}")



# 2,500rpm 保持の音の案内（運転席から PC を見なくてよいように）
# (周波数 Hz, 長さ ms) の並び。0 Hz は無音。
_BEEPS = {
    "low": [(450, 220), (0, 80), (450, 220)],                       # 低い「ブッ、ブッ」= もっと踏む
    "high": [(1800, 130), (0, 60), (1800, 130), (0, 60), (1800, 130)],  # 高い「ピピピ」= 踏みすぎ
    "ok": [(1000, 120), (0, 60), (1300, 160)],                      # 「ピポッ」= ちょうどいい
    "start": [(1300, 400)],                                         # 記録開始
    "done": [(900, 1200)],                                          # 長い「ピー」= 終わり。アクセルを戻す
}
_BEEP_TEXT = {"low": "↑ もっと踏む", "high": "↓ 踏みすぎ", "ok": "◎ ちょうどいい（そのまま 3 秒）",
              "start": "● 記録中…そのまま保って", "done": "■ 終わり。アクセルを戻してください"}
_WAV_CACHE = {}


def _square_wav(tones, rate=22050, volume=0.95):
    """矩形波の WAV を作る。正弦波（winsound.Beep）より耳につきやすく、エンジン音の中でも聞こえやすい。"""
    import io
    import struct
    import wave
    frames = bytearray()
    peak = int(32767 * volume)
    for freq, ms in tones:
        n = int(rate * ms / 1000)
        if freq <= 0:
            frames += b"\x00\x00" * n
            continue
        half = rate / freq / 2
        for i in range(n):
            frames += struct.pack("<h", peak if int(i / half) % 2 == 0 else -peak)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def beep(kind):
    print(f"\n   {_BEEP_TEXT[kind]}", flush=True)
    try:
        import winsound
        if kind not in _WAV_CACHE:
            _WAV_CACHE[kind] = _square_wav(_BEEPS[kind])
        winsound.PlaySound(_WAV_CACHE[kind], winsound.SND_MEMORY)
    except (ImportError, RuntimeError):
        print("\a", end="", flush=True)  # Windows 以外は端末のベル


def _enable_colors():
    if os.name == "nt":
        os.system("")  # Windows のコンソールで色の指定（ANSI）を有効にする


def rpm_gauge(rpm, low, high, top=4000, width=40):
    """タコメーター風の回転ゲージを 1 行で表示する（範囲内は緑、低いと黄、高いと赤）。"""
    filled = max(0, min(width, round(rpm / top * width)))
    lo_mark, hi_mark = round(low / top * width), round(high / top * width)
    cells = []
    for i in range(width):
        if i < filled:
            cells.append("█")
        elif lo_mark <= i < hi_mark:
            cells.append("░")  # 目標の範囲
        else:
            cells.append("·")
    if low <= rpm <= high:
        color, mark = "\033[92m", "◎ ちょうどいい"
    elif rpm < low:
        color, mark = "\033[93m", "↑ もっと踏む"
    else:
        color, mark = "\033[91m", "↓ 踏みすぎ"
    print(f"\r   {color}{''.join(cells)} {rpm:>5} rpm  {mark}\033[0m   ", end="", flush=True)


def ask_yes(prompt):
    """[Enter = はい / n = いいえ]。対話できないとき（テスト・リダイレクト）は「いいえ」。"""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    return input(prompt).strip().lower() not in ("n", "no", "いいえ")


def ask_choice(prompt, choices, default):
    """番号で選ぶ。Enter は default。対話できないときは None。"""
    if not sys.stdin or not sys.stdin.isatty():
        return None
    while True:
        ans = input(prompt).strip() or default
        if ans in choices:
            return ans
        print(f"  {' / '.join(choices)} のどれかを入れてください")


def run_cranking(elm, report, reconnect, demo_fake=None):
    """エンジンがかからない車: セルを回している間の電圧・回転数・吸気管圧力を記録する。"""
    supported = {int(p, 16) for p in report.get("supported_pids", [])}
    print("\n" + "=" * 50)
    print("キーを回してセルを回してください（5〜10 秒。1 分以内に）。")
    print("記録は自動で始まり、セルを止めて 3 秒たつと終わります（やめるときは Ctrl+C）。")
    if demo_fake:
        # デモ: 静止電圧を読んだあとでセルを回したことにする
        rest_read = elm.battery_voltage

        def demo_voltage():
            v = rest_read()
            demo_fake.voltage, demo_fake.engine_running, demo_fake.rpm = 9.8, True, 220
            return v
        elm.battery_voltage = demo_voltage
    show = lambda r: print(f"   {r['t']:>5.1f} 秒  電圧 {r['voltage'] if r['voltage'] is not None else '-':>5} V  "
                           f"回転 {r['rpm'] if r['rpm'] is not None else '-':>5} rpm  "
                           f"吸気管圧力 {r['map_kpa'] if r['map_kpa'] is not None else '-'} kPa", flush=True)
    try:
        crank = with_retry(elm, reconnect, record_cranking, elm, supported,
                           max_seconds=0.05 if demo_fake else 20.0, poll=0 if demo_fake else 0.1, on_sample=show)
    except KeyboardInterrupt:
        print("\nクランキングの記録はやめました。")
        return None
    if crank is None:
        print("セルが回されたのを検知できませんでした（電圧が下がらず、回転も出ませんでした）。")
        return None
    print(f"\n■ クランキングの記録: 静止時 {crank['resting_voltage']} V → 最低 {crank['min_voltage']} V、"
          f"最高回転 {crank['max_rpm']} rpm"
          + ("（エンジンがかかりました）" if crank["started"] else ""))
    if not crank["rpm_seen"]:
        print("   ※ ECU が回転を認識していません（回転数 0 のまま）。")
    return crank


def with_retry(elm, reconnect, func, *a, **kw):
    """読み取り中に通信が切れたら（始動時の電圧低下など）、1 回だけ繋ぎ直してやり直す。"""
    try:
        return func(*a, **kw)
    except ObdError:
        reconnect()
        return func(*a, **kw)


def wait_for_engine(elm, reconnect, timeout=300, poll=1.0, on_poll=None):
    """エンジン始動（回転数 > 0）を待つ。始動したら回転数、時間切れなら None。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if on_poll:
            on_poll()
        rpm = read_rpm(elm)
        if rpm is None:
            # クランキング中の電圧低下でアダプタや ECU との通信が切れることがある
            try:
                reconnect()
            except ObdError:
                pass
        elif rpm > 0:
            return rpm
        time.sleep(poll)
    return None


def print_stage(title, st):
    print(f"\n■ {title}（{st['measured_at'][11:16]}）")
    for v in st["live"]:
        print(f"   {v['name']:<16} {v['value']} {v['unit']}")
    t = st.get("trend")
    if t:
        print(f"   ― {t['seconds']} 秒間の動き ―")
        for it in t["items"]:
            line = f"   {it['name']:<16} 最小 {it['min']} ／ 最大 {it['max']} ／ 平均 {it['avg']} {it['unit']}"
            if "switches" in it:
                line += f"（0.45V をまたいだ回数: {it['switches']} 回）"
            print(line)
    if st.get("pending_dtcs"):
        print("   保留中の故障コード: " + "、".join(c["code"] for c in st["pending_dtcs"]))


def demo_beep(kind):
    print(f"   {_BEEP_TEXT[kind]}")


def run_stages(elm, report, reconnect, demo_fake=None):
    """キー ON の読み取りのあと、エンジン始動 → アイドリング →（暖機）→（2,500rpm 保持）を続けて測る。"""
    supported = {int(p, 16) for p in report.get("supported_pids", [])}
    stages = {}
    pause = 0 if demo_fake else 1

    print("\n" + "=" * 50)
    print("エンジンをかけてください。始動は自動で検知します（やめるときは Ctrl+C）。")
    try:
        rpm = wait_for_engine(elm, reconnect, poll=pause,
                              on_poll=(lambda: setattr(demo_fake, "engine_running", True)) if demo_fake else None)
    except KeyboardInterrupt:
        print("\nエンジン始動後の測定はやめました。")
        return stages
    if rpm is None:
        print("始動を検知できませんでした。エンジン停止中のデータだけで診断します。")
        return stages
    print(f"始動を確認しました（{rpm} rpm）。回転が落ち着くまで 10 秒待ちます...")
    time.sleep(10 * pause)

    print("アイドリングのデータを読んでいます（30 秒ほど）...")
    stages["idle_after_start"] = with_retry(elm, reconnect, measure_running, elm, supported, 20)
    print_stage("始動直後のアイドリング", stages["idle_after_start"])

    coolant = read_coolant(elm)
    if coolant is not None and coolant < 80 and ask_yes(
            f"\n水温は {coolant}℃ です。80℃ まで暖機を待ちますか？ [Enter = 待つ / n = 待たない]: "):
        print("暖機を待ちます。10 秒ごとに記録します（途中でやめるときは Ctrl+C）。")
        log = []
        try:
            while True:
                row = with_retry(elm, reconnect, warmup_sample, elm, supported)
                log.append(row)
                print(f"   {row['time'][11:19]}  水温 {row.get('05', '-')}℃  回転 {row.get('0C', '-')} rpm  "
                      f"短期補正 {row.get('06', '-')}%  O2 {row.get('14', '-')}V")
                if row.get("05") is not None and row["05"] >= 80:
                    break
                time.sleep(10 * pause)
        except KeyboardInterrupt:
            print("\n暖機の待機をやめました。")
        stages["warmup_log"] = log
        print("暖機後のアイドリングを読んでいます（30 秒ほど）...")
        stages["idle_warm"] = with_retry(elm, reconnect, measure_running, elm, supported, 20)
        print_stage("暖機後のアイドリング", stages["idle_warm"])

    if ask_yes("\n2,500rpm で保持して測りますか？（触媒・リア O2 の確認用） [Enter = はい / n = いいえ]: "):
        print("\n運転席に移って、アクセルで回転を上げてください（2 分以内）。PC を見なくても音で案内します。")
        print("   低い「ブッ、ブッ」= もっと踏む ／ 高い「ピピピ」= 踏みすぎ")
        print("   「ピポッ」= ちょうどいい（2,200〜2,800rpm）→ 3 秒保つと自動で記録開始（15 秒）")
        print("   長い「ピー」= 終わり。アクセルを戻してください")
        print("   画面の回転ゲージも、ちょうどいいと緑になります（PC の音量は最大がおすすめ）")
        if demo_fake:
            demo_fake.rpm = 2500
        _enable_colors()
        hold = with_retry(elm, reconnect, hold_rpm_guided, elm, supported, demo_beep if demo_fake else beep,
                          poll=0.3 * pause, settle=3.0 * pause, on_rpm=rpm_gauge)
        print()
        if demo_fake:
            demo_fake.rpm = 800
        if hold is None:
            print("2 分たっても回転が範囲に入らなかったので、2,500rpm の測定はとばします。")
        else:
            stages["hold_2500rpm"] = hold
            print_stage(f"2,500rpm 保持（範囲内だった割合 {int((hold['in_range_ratio'] or 0) * 100)}%）", hold)
    return stages


def main(argv=None):
    ap = argparse.ArgumentParser(description="OBD2 の故障コード・ライブデータを読み取る")
    ap.add_argument("port", nargs="?", help="アダプタの COM ポート（例: COM7）")
    ap.add_argument("--baudrate", type=int, default=38400)
    ap.add_argument("--live", action="store_true", help="ライブデータを表示し続ける")
    ap.add_argument("--csv", action="store_true", help="--live のとき CSV にも記録する")
    ap.add_argument("--pids", metavar="一覧",
                    help="--live で見る項目を絞る（例: 15 や 15,14,0C。o2 で O2 センサ 2 つと回転数）。少ないほど速く更新")
    ap.add_argument("--demo", action="store_true", help="アダプタなしでシミュレータに対して実行")
    ap.add_argument("--no-ai", action="store_true", help="Claude による診断をしない")
    ap.add_argument("--single", action="store_true",
                    help="エンジン始動をはさんだ段階ごとの測定をせず、今の状態を 1 回だけ読む")
    ap.add_argument("--trend", type=float, default=10.0, metavar="秒",
                    help="エンジン稼働中に続けて読む秒数（既定 10、0 で読まない）")
    ap.add_argument("--from-scan", metavar="JSON", nargs="?", const="latest",
                    help="保存済みのスキャン結果で Claude に診断させる（車に繋がない）。省略時は最新のもの")
    args = ap.parse_args(argv)

    os.makedirs("scan_logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.from_scan:
        if args.from_scan == "latest":
            scans = sorted(glob.glob(os.path.join("scan_logs", "scan_*.json")))
            if not scans:
                print("scan_logs フォルダに読み取り結果がありません。先に run_scan.bat で車を読み取ってください。")
                return 1
            args.from_scan = scans[-1]
            print(f"読み取り結果: {args.from_scan}")
        with open(args.from_scan, encoding="utf-8") as f:
            report = json.load(f)
        report.pop("ai_diagnosis", None)
        print_report(report)
        run_ai(report, stamp)
        return 0

    demo_fake = None
    if args.demo:
        from obd_scanner.simulator import FakeElm327
        port = demo_fake = FakeElm327(vehicle_protocol="6", needs_kw0=False)
        demo_fake.engine_running = False  # デモでは「キー ON → 始動」の流れを見せる
        saved = None
    else:
        port = ask_port(args.port)
        if port is None:
            return 2
        saved = load_init_settings()

    try:
        elm = Elm327(port, baudrate=args.baudrate)
    except ObdError as e:
        print(f"接続できません: {e}")
        return 1
    if not args.demo:
        remember_port(port)
    # 車とのやり取りをすべて記録する（読み取れない項目があったときの調査用）
    raw_path = os.path.join("scan_logs", f"raw_{stamp}.txt")
    raw_file = open(raw_path, "w", encoding="utf-8")
    elm.log = lambda cmd, reply: raw_file.write(
        f"> {cmd:<14} {' | '.join(x.strip() for x in reply.replace('>', '').splitlines() if x.strip())}\n")
    print("車両に接続しています...")
    try:
        protocol, extra, reset, label = connect_any(elm, saved)
    except ObdError as e:
        print(f"\n接続できません: {e}")
        print("・キーは ON ですか？")
        print("・それでもだめなら run_kline_test.bat でほかの設定を探してください")
        elm.close()
        raw_file.close()
        print(f"通信の記録: {raw_path}")
        return 1
    print(f"接続しました: {elm.protocol_name}")
    if not args.demo and label != "前回つながった設定":
        save_init_settings(protocol, extra, reset, label)

    try:
        if args.live:
            live_loop(elm, os.path.join("scan_logs", f"live_{stamp}.csv") if args.csv else None,
                      interval=0 if args.pids else 0.5,
                      only=parse_pid_list(args.pids) if args.pids else None)
            return 0
        print("読み取り中です（30 秒ほど。エンジンがかかっていれば、さらに O2 センサなどの動きを見ます）...")
        report = full_scan(elm, trend_seconds=args.trend)
        report["scanned_at"] = datetime.now().isoformat(timespec="seconds")
        print_report(report)
        if report.get("generic_obd") and not report.get("engine_running") and not args.single:
            choice = "1" if demo_fake else ask_choice(
                "\n次はどうしますか？\n"
                "  1 = エンジンをかけて、アイドリングなども測る\n"
                "  2 = エンジンがかからない（セルを回している間を記録する）\n"
                "  3 = ここで終わる\n"
                "番号を入れて Enter（何も入れなければ 1）: ", ("1", "2", "3"), "1")
            reconnect = lambda: elm.connect(protocol=protocol, extra_init=extra, reset=reset)
            if choice == "1":
                stages = run_stages(elm, report, reconnect, demo_fake=demo_fake)
                if stages:
                    report["stages"] = stages
            elif choice == "2":
                crank = run_cranking(elm, report, reconnect)
                if crank:
                    report["stages"] = {"cranking": crank}
    finally:
        elm.close()
        raw_file.close()

    path = os.path.join("scan_logs", f"scan_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n保存しました: {path}")
    print(f"通信の記録: {raw_path}")
    if not args.no_ai:
        run_ai(report, stamp, report_path=path)
    return 0


if __name__ == "__main__":
    sys.exit(run_from_console(main))
