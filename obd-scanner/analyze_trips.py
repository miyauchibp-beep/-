r"""走行中に記録したログを、1 回の運転ごとにまとめ、乗り方のクセを出す。

使い方（Windows）: ログのファイルを run_trips.bat の上にドラッグ＆ドロップ（複数可）、または
    python analyze_trips.py scan_logs\live_20261101_100000.csv
結果は scan_logs\trips_日時.json に保存する。診断に使うときは
    python obd_scan.py COM6 --trips scan_logs\trips_日時.json
"""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from obd_scanner.console import run_from_console
    from obd_scanner.trips import analyze
except ImportError as e:
    print(f"読み込めないファイルがあります（{e}）。")
    if os.name == "nt":
        input("\nEnter キーを押すと閉じます...")
    sys.exit(1)


def show(result):
    h = result["habits"]
    print(f"\n■ 運転 {h['trips']} 回" + (f"（合計 {h['total_minutes']} 分・{h['total_km']} km）" if h["trips"] else ""))
    for t in result["trips"]:
        when = datetime.fromtimestamp(t["start"]).strftime("%m/%d %H:%M") if t["start"] > 1e9 else f"{t['start']:.0f} 秒"
        coolant = f"水温 {t['coolant_start']:.0f}→{t['coolant_max']:.0f}℃" if t["coolant_max"] is not None else "水温 -"
        km = f"{t['km']} km" if t["km"] is not None else "- km"
        print(f"   {when}  {t['minutes']:>5} 分  {km:>8}  {coolant}  最高 {t['max_rpm']:.0f} 回転"
              + ("  ※冷えたまま停止" if t["stopped_cold"] else ""))
    print("\n■ 乗り方のクセ")
    for note in h["notes"]:
        print(f"   ・{note}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="走行ログを運転ごとにまとめ、乗り方のクセを出す")
    ap.add_argument("logs", nargs="*", help="ライブデータ CSV・スマホの記録 CSV・スキャン結果 JSON（複数可）")
    args = ap.parse_args(argv)
    if not args.logs:
        print("ログのファイルを run_trips.bat の上にドラッグ＆ドロップしてください。")
        return 1
    result = analyze(args.logs)
    show(result)
    os.makedirs("scan_logs", exist_ok=True)
    path = os.path.join("scan_logs", f"trips_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"sources": [os.path.basename(p) for p in args.logs], **result}, f, ensure_ascii=False, indent=2)
    print(f"\n保存しました: {path}")
    print("診断に使うときは: python obd_scan.py COM6 --trips " + path)
    return 0


if __name__ == "__main__":
    sys.exit(run_from_console(main))
