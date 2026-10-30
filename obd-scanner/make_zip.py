"""PC に配る ZIP（dist/OBD_TEST.zip）を作る。展開すると OBD_TEST フォルダ 1 つになる。"""

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [
    "obd_scan.py",
    "run_scan.bat",
    "run_diag_last.bat",
    "record_result.py",
    "run_result.bat",
    "kline_init_test.py",
    "run_kline_test.bat",
    "obd_scanner/__init__.py",
    "obd_scanner/elm327.py",
    "obd_scanner/pids.py",
    "obd_scanner/dtc.py",
    "obd_scanner/simulator.py",
    "obd_scanner/scan.py",
    "obd_scanner/console.py",
    "obd_scanner/ai_diag.py",
    "obd_scanner/cases.py",
    # PC の Claude Code で作業を続けるための経緯・構想メモ
    "HANDOFF.md",
    "APP_SPEC.md",
    "ROADMAP.md",
    "README.md",
]

README = """OBD スキャンツール

準備
  スマホの Bluetooth をオフ、Car Scanner を閉じる。
  車にアダプタをさして、キーを ON（エンジンは OFF のままでよい）。
  （青い「PC が保護されました」が出たら「詳細情報」→「実行」）
  起動すると COM ポートを聞かれます。Windows の「その他の Bluetooth 設定」→「COM ポート」で
  「OBDII」の「発信」になっている番号（2026-10-12 時点では COM7）。一度つながると次回から覚えています。

■ run_scan.bat … 故障コード・フリーズフレーム・ライブデータを読み、Claude が診断
  キー ON（エンジン停止）で読んだあと「次はどうしますか？」と聞かれます。
  1（Enter）→ エンジンをかける（自動で検知）→ アイドリング →（暖機・2,500rpm は聞かれたら選ぶ）
  2 → エンジンがかからない車。セルを回すと、その間の電圧・回転数を自動で記録します
  3 → ここで終わる
  4 → 回転数を気にせず記録（ハイブリッド・メンテナンスモード・アイドリングの様子見）
      何分記録するか聞かれます（Enter で 5 分）。アクセル操作は不要で、2 秒ごとに読んで自動で終わります。
      症状が出たら スペースキー で ★ 印（前後の値の変化を Claude に渡します）、早く終えるときは q。
      走りながら記録するときは、助手席の人に押してもらう（運転中は操作しない）
      ハイブリッド車と判定できたときは、4 がおすすめとして既定になります
  2,500rpm は運転席でアクセルを踏むだけ。PC が音で案内します（低い音 = もっと踏む、高い音 = 踏みすぎ、
  「ピピッ」= ちょうどいい → 3 秒保つと自動で記録、長い「ピー」= 終わり）。音が聞こえるよう PC の音量を上げておく
  最後に「車両」「症状」を聞かれるので入力（空のまま Enter でも可）。全部まとめて Claude が診断します。
  「Claude に診断させますか？」で p を入れると、API を使わず、チャットに貼る文（scan_logs の prompt_日時.txt）
  を作ります。メモ帳で開いて全部コピーし、claude.ai のチャットに貼れば API 代なしで診断できます
  （API キーが未設定のときも自動でこちらになります）。
  車によっては「モード 06（ECU の自己検査）」も読み、不合格の項目と「自動チェック」の目安を表示します。
  結果は scan_logs フォルダに保存されます（scan_日時.json、診断は diag_日時.md）。
  Claude の診断には API キーが必要です。未設定なら、コマンドプロンプトで一度だけ
    setx ANTHROPIC_API_KEY "sk-ant-で始まるキー"
  を実行し、画面を開き直してください。

■ run_diag_last.bat … 車をつながずに、いちばん新しい読み取り結果を Claude に診断させる
  ライブデータを見続けたいときは、このフォルダのアドレス欄に cmd と入れて Enter し、
    python obd_scan.py COM7 --live
  （CSV にも記録するなら --live --csv。Ctrl+C で終了）

■ run_result.bat … 修理が終わったら、実際の原因を記録する（答え合わせ）
  診断メモの一覧（新しい順）から番号を選び、実際の原因・作業・Claude の見立てが当たったか・決め手を入力。
  scan_logs フォルダの cases.jsonl と診断メモの末尾に記録され、Excel で開ける cases.csv も作ります。
  起動時に、これまでの的中率（1 位で当たり・候補に入っていた）を表示します。
  同じ車種（較正 ID が同じ）を次に診断するとき、この記録を Claude に参考として渡します。
  お客様の名前・電話番号は入れないでください。

■ run_kline_test.bat … 繋がらない車で、接続設定を探す
  繋がった設定は working_init.json に保存され、run_scan.bat が自動で使います。

いずれも最後に「Enter キーを押すと閉じます」と出たら、
画面の文字を全部コピーして Claude に貼ってください。

車に送るのは読み取りだけです。故障コードの消去などはしません。
COM4 / COM5（スナップオン）は使いません。
"""


def main():
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    out = os.path.join(HERE, "dist", "OBD_TEST.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(os.path.join(HERE, f), "OBD_TEST/" + f)
        # Windows のメモ帳で文字化けしないよう BOM 付き UTF-8・CRLF にする
        z.writestr("OBD_TEST/README.txt", "﻿" + README.replace("\n", "\r\n"))
    print(out)


if __name__ == "__main__":
    main()
