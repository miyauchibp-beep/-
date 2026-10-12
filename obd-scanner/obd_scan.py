r"""車の故障コード・フリーズフレーム・ライブデータを読み取り、Claude に診断させる（車へは読み取りのみ）。

使い方（Windows）:
    python obd_scan.py COM7            # 読み取って保存 → 車両情報・症状を聞いて Claude が診断
    python obd_scan.py COM7 --no-ai    # 読み取りと保存だけ
    python obd_scan.py COM7 --live     # ライブデータを表示し続ける（Ctrl+C で終了）
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
    from obd_scanner.scan import full_scan, read_live, supported_pids
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


def live_loop(elm, csv_path=None, interval=0.5):
    supported = set(supported_pids(elm))
    pid_list = [p for p in pids.DEFAULT_LIVE if p in supported]
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
                writer.writerow([datetime.now().isoformat(timespec="seconds")]
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="OBD2 の故障コード・ライブデータを読み取る")
    ap.add_argument("port", nargs="?", help="アダプタの COM ポート（例: COM7）")
    ap.add_argument("--baudrate", type=int, default=38400)
    ap.add_argument("--live", action="store_true", help="ライブデータを表示し続ける")
    ap.add_argument("--csv", action="store_true", help="--live のとき CSV にも記録する")
    ap.add_argument("--demo", action="store_true", help="アダプタなしでシミュレータに対して実行")
    ap.add_argument("--no-ai", action="store_true", help="Claude による診断をしない")
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

    if args.demo:
        from obd_scanner.simulator import FakeElm327
        port = FakeElm327(vehicle_protocol="6", needs_kw0=False)
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
            live_loop(elm, os.path.join("scan_logs", f"live_{stamp}.csv") if args.csv else None)
            return 0
        print("読み取り中です（30 秒ほど。エンジンがかかっていれば、さらに O2 センサなどの動きを見ます）...")
        report = full_scan(elm, trend_seconds=args.trend)
        report["scanned_at"] = datetime.now().isoformat(timespec="seconds")
    finally:
        elm.close()
        raw_file.close()

    print_report(report)
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
