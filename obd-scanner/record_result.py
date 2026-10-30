r"""修理が終わったあとに、実際の原因と Claude の見立てが当たったかを記録する（答え合わせ）。

使い方（Windows）: run_result.bat をダブルクリック、または
    python record_result.py

scan_logs の診断メモ（diag_日時.md）から選び、実際の原因・作業・当たり外れを入力すると、
scan_logs\cases.jsonl に追記し、診断メモの末尾にも書き足す。
同じ車種（較正 ID が同じ）の記録は、次回の診断で Claude に参考として渡す。
お客様の名前・電話番号は入れないでください。
"""

import os
import sys

try:
    from obd_scanner.cases import (VERDICTS, hit_rate, make_case, recent_diags, save_case, scan_for,
                                   verdict_from_rank, write_csv)
    from obd_scanner.ai_diag import AiDiagError
    from obd_scanner.console import run_from_console
    from obd_scanner.remote import load_server, remote_answer
except ImportError as e:
    print(f"読み込めないファイルがあります（{e}）。")
    print("obd_scanner フォルダが見つかりません。record_result.py と同じ場所にフォルダごとコピーしてください。")
    if os.name == "nt":
        input("\nEnter キーを押すと閉じます...")
    sys.exit(1)


def ask(prompt):
    return input(prompt).strip()


def print_hit_rate():
    n, top1, listed = hit_rate()
    if n:
        print(f"これまでの答え合わせ {n} 件: 1 位で当たり {top1} 件（{top1 * 100 // n}%）、"
              f"候補に入っていた {listed} 件（{listed * 100 // n}%）\n")


def main():
    print_hit_rate()
    diags = recent_diags()
    if not diags:
        print("scan_logs フォルダに診断メモ（diag_日時.md）がありません。先に run_scan.bat で診断してください。")
        return 1
    print("答え合わせをする診断を選んでください（新しい順）")
    for i, d in enumerate(diags, 1):
        s = d["stamp"]
        when = f"{s[4:6]}/{s[6:8]} {s[9:11]}:{s[11:13]}" if len(s) == 15 else s
        mark = "（記録済み）" if d["answered"] else ""
        print(f"  {i:>2} = {when}  {d['vehicle'] or '車両未入力'} ／ {d['symptom'] or '症状未入力'} {mark}")
    pick = ask("番号を入れて Enter（何も入れなければ 1）: ") or "1"
    if not pick.isdigit() or not 1 <= int(pick) <= len(diags):
        print("番号が正しくありません。")
        return 1
    diag = diags[int(pick) - 1]
    if diag["answered"] and ask("この診断は記録済みです。もう 1 件書き足しますか？ [y = はい]: ").lower() != "y":
        return 0

    print("\n※ お客様の名前・電話番号は入れないでください（次の診断で Claude に送ります）")
    cause = ""
    while not cause:
        cause = ask("実際の原因（例: プラグのかぶり、O2 センサ不良）: ")
    work = ask("作業・交換部品（例: プラグ 3 本交換、バッテリー充電）: ")
    hit_rank = None
    if diag["candidates"]:
        # 決まった形の診断なら、当たった候補の番号を選ぶ（当たり外れはそこから決まる）
        print("Claude の原因候補（可能性の高い順）のうち、当たったのはどれ？")
        for n, title in enumerate(diag["candidates"], 1):
            print(f"  {n} = {title}")
        print("  0 = どれでもない（外れ）")
        choices = [str(n) for n in range(len(diag["candidates"]) + 1)]
        pick = ""
        while pick not in choices:
            pick = ask("番号を入れて Enter: ")
        hit_rank = int(pick)
        verdict = verdict_from_rank(hit_rank)
    else:
        print("Claude の見立ては？")
        for k, v in VERDICTS.items():
            print(f"  {k} = {v}")
        verdict = ""
        while verdict not in VERDICTS:
            verdict = ask("番号を入れて Enter: ")
    note = ask("決め手・メモ（例: プラグの臭いで判断、車を貸していた）: ")

    case = make_case(diag, scan_for(diag), cause, work, verdict, note, hit_rank=hit_rank)
    save_case(case, md_path=diag["path"])
    write_csv()
    print(f"\n記録しました: scan_logs\\cases.jsonl と {os.path.basename(diag['path'])}")
    server = load_server()
    if server and diag.get("server_scan_id"):
        # 中継サーバー経由の診断なら、答え合わせもサーバーに送る（仲間の次の診断に使われる）
        try:
            remote_answer(server, diag["server_scan_id"], cause, work, hit_rank, note)
            print("中継サーバーにも記録しました。")
        except AiDiagError as e:
            print(f"中継サーバーには送れませんでした（PC には記録済み）: {e}")
    print("一覧（Excel で開けます）: scan_logs\\cases.csv")
    if case["calibration_id"]:
        print(f"同じ較正 ID（{case['calibration_id']}）の車を診断するとき、Claude に参考として渡します。")
    else:
        print("較正 ID がわからない記録なので、次の診断には自動では渡しません。")
    return 0


if __name__ == "__main__":
    sys.exit(run_from_console(main))
