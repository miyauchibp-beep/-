"""PC に配る ZIP（dist/OBD_TEST.zip）を作る。展開すると OBD_TEST フォルダ 1 つになる。"""

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [
    "kline_init_test.py",
    "run_kline_test.bat",
    "obd_scanner/__init__.py",
    "obd_scanner/elm327.py",
    "obd_scanner/pids.py",
    "obd_scanner/dtc.py",
    "obd_scanner/simulator.py",
]

README = """OBD テスト（K-line 接続テスト）

使い方
  1. スマホの Bluetooth をオフ、Car Scanner を閉じる
  2. 車にアダプタをさして、キーを ON（エンジンは OFF のままでよい）
  3. このフォルダの run_kline_test.bat をダブルクリック
     （青い「PC が保護されました」が出たら「詳細情報」→「実行」）
  4. 数分待つ。最後に「Enter キーを押すと閉じます」と出たら、
     画面の文字を全部コピーして Claude に貼る

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
