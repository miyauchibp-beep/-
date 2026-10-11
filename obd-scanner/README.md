# OBD スキャンツール（ELM327 / K-line 対応）

ELM327 互換アダプタで、車の故障コード・ライブデータを PC から読むためのツールです。
python-OBD を使わず、アダプタに直接 AT コマンドを送るので、**初期化の手順を細かく変えられます**。

## いまの課題と、まず試すこと

平成17年式アレックス（NZE121、K-line / KWP、プロトコル4）で、
PC からだと `BUS INIT: ...ERROR` になり ECU に繋がりません（スマホの Car Scanner では繋がる）。

`kline_init_test.py` は、互換アダプタで効くことが多い設定（キーワード検査オフ `ATKW0` など）を
10 通り順番に試し、繋がった設定を `working_init.json` に保存します。

### 手順（Windows）

1. このフォルダ（`kline_init_test.py` と `obd_scanner` フォルダ）を `C:\Users\Owner\Downloads\OBD` に置く
2. スマホの Bluetooth をオフにし、Car Scanner を閉じる
3. 車のキーを ON（エンジンは OFF、電圧が低ければ安定化電源をつなぐ）
4. コマンドプロンプトで:

   ```
   cd C:\Users\Owner\Downloads\OBD
   python kline_init_test.py COM6
   ```

5. 終わったら `kline_logs\kline_test_日時.txt`（と、成功していれば `working_init.json`）を Claude に渡す

- 全部で数分かかります（設定の切り替えごとに 5 秒待ちます。K-line は続けて初期化すると失敗しやすいため）
- 成功しても全部試したいとき: `python kline_init_test.py COM6 --all`
- 特定の番号だけ: `python kline_init_test.py COM6 --only 2 3`
- アダプタなしで動きを見る: `python kline_init_test.py --demo`

### 安全のための決まり

- 車に送るのは**読み取りの要求（01 00）だけ**です。故障コードの消去や書き込みはしません
- スナップオン診断機（NANO-BT2）の **COM4 / COM5 は指定しても拒否**します

## 構成

| ファイル | 内容 |
| --- | --- |
| `kline_init_test.py` | K-line 初期化設定の自動テスト |
| `obd_scanner/elm327.py` | アダプタとの通信、応答の解析（K-line / CAN 両対応） |
| `obd_scanner/pids.py` | ライブデータ（回転数・水温など）の換算、レディネス |
| `obd_scanner/dtc.py` | 故障コードの読み取りと日本語説明 |
| `obd_scanner/simulator.py` | 動作確認用の疑似アダプタ |
| `tests/` | テスト（`python -m unittest discover -s tests`） |

繋がる設定がわかったら、`Elm327(...).connect(protocol="4", extra_init=["ATKW0"])` のように
その設定で接続でき、`obd_ai_diag.py` の通信部分をこれに置き換える予定です。
