"""PC に配る ZIP（dist/OBD_TEST.zip）を作る。展開すると OBD_TEST フォルダ 1 つになる。"""

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [
    "obd_scan.py",
    "run_scan.bat",
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
]

README = """OBD スキャンツール

準備
  スマホの Bluetooth をオフ、Car Scanner を閉じる。
  車にアダプタをさして、キーを ON（エンジンは OFF のままでよい）。
  （青い「PC が保護されました」が出たら「詳細情報」→「実行」）

■ run_scan.bat … 故障コード・フリーズフレーム・ライブデータを読み、Claude が診断
  読み取り後に「車両」「症状」を聞かれるので入力（空のまま Enter でも可）。
  結果は scan_logs フォルダに保存されます（scan_日時.json、診断は diag_日時.md）。
  Claude の診断には API キーが必要です。未設定なら、コマンドプロンプトで一度だけ
    setx ANTHROPIC_API_KEY "sk-ant-で始まるキー"
  を実行し、画面を開き直してください。
  保存済みの結果で診断し直す: python obd_scan.py --from-scan scan_logs\scan_日時.json
  ライブデータを見続けたいときは、このフォルダのアドレス欄に cmd と入れて Enter し、
    python obd_scan.py COM6 --live
  （CSV にも記録するなら --live --csv。Ctrl+C で終了）

■ run_kline_test.bat … 繋がらない車で、接続設定を探す
  繋がった設定は working_init.json に保存され、run_scan.bat が自動で使います。

どちらも最後に「Enter キーを押すと閉じます」と出たら、
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
