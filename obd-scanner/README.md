# OBD スキャンツール（ELM327 / K-line 対応）

ELM327 互換アダプタで、車の故障コード・ライブデータを PC から読むためのツールです。
python-OBD を使わず、アダプタに直接 AT コマンドを送るので、**初期化の手順を細かく変えられます**。

## いまの課題と、まず試すこと

平成17年式アレックス（NZE121、K-line / KWP、プロトコル4）で、
PC からだと `BUS INIT: ...ERROR` になり ECU に繋がりません（スマホの Car Scanner では繋がる）。

`kline_init_test.py` は、初期化の設定を順番に試し、繋がった設定を `working_init.json` に保存します。

- 1 回目（`--round 1`）: 一般的な設定 10 通り。2026-10-12 の実車テストでは全滅。
  `BUS INIT: ...ERROR` で、共通規格の宛先 33 への 5baud 呼びかけに ECU が返事をしなかった
- 2 回目（既定）: 呼びかける宛先（`ATIIA`）を変えて 15 通り。国内独自の車は 01 00 に答えないことがあるので、
  初期化できたら 1A（ECU 情報）・21（メーカー独自データ）などの読み取り要求も試す。
  2026-10-12 の実車テスト（12.5V）では、15 通りすべて失敗
- 2026-10-12 トヨタ bB（QNC21, CAN）では自動判別で成功（プロトコル 6、対応PID 19 個）。
  PC → Bluetooth → アダプタ → ECU の経路は正常で、問題はアレックスの K-line 初期化に限られる
- 次の手がかり: Car Scanner が実際に送っている設定（接続プロファイル・通信ログ）と、
  新しい CAN の車で PC → アダプタ → ECU の経路が正常か（`--round 1 --only 10`）

### 手順（Windows）

1. このフォルダ（`kline_init_test.py` と `obd_scanner` フォルダ）を `C:\Users\Owner\Downloads\OBD` に置く
2. スマホの Bluetooth をオフにし、Car Scanner を閉じる
3. 車のキーを ON（エンジンは OFF、電圧が低ければ安定化電源をつなぐ）
4. `run_kline_test.bat` をダブルクリック（COM6 で実行し、終わっても画面は閉じません）。
   または、コマンドプロンプトで:

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

- 車に送るのは**読み取りの要求（01 / 03 / 1A / 21）だけ**です。故障コードの消去や書き込みはしません
- スナップオン診断機（NANO-BT2）の **COM4 / COM5 は指定しても拒否**します

## 故障コード・ライブデータの読み取りと AI 診断（obd_scan.py）

`run_scan.bat` をダブルクリック、または `python obd_scan.py COM6`。
車台番号・警告灯・レディネス・故障コード（現在／保留中／消去不可）・フリーズフレーム・ライブデータを読み、
`scan_logs\scan_日時.json` に保存します。

続けて車両情報と症状を聞き、Claude（`claude-opus-5-5`）に原因候補（可能性順）・確認手順・修理方法を出させて
`scan_logs\diag_日時.md` に保存します。

- API キーは環境変数 `ANTHROPIC_API_KEY`（Windows: `setx ANTHROPIC_API_KEY "sk-ant-..."` のあと画面を開き直す）
- 安全上の判定で断られたときは、サーバー側で別モデルに自動で切り替える設定（`fallbacks: "default"`）にしています
- 診断しない: `--no-ai` ／ 保存済みの結果で診断し直す: `--from-scan scan_logs\scan_日時.json`

- ライブデータを表示し続ける: `python obd_scan.py COM6 --live`（`--csv` で記録も）
- `working_init.json` があれば、その接続設定を使います

## 構成

| ファイル | 内容 |
| --- | --- |
| `obd_scan.py` | 故障コード・フリーズフレーム・ライブデータの読み取りと保存 |
| `kline_init_test.py` | K-line 初期化設定の自動テスト |
| `obd_scanner/elm327.py` | アダプタとの通信、応答の解析（K-line / CAN 両対応） |
| `obd_scanner/pids.py` | ライブデータ（回転数・水温など）の換算、レディネス |
| `obd_scanner/dtc.py` | 故障コードの読み取りと日本語説明 |
| `obd_scanner/scan.py` | 一通りの読み取り（モード 01/02/03/07/09/0A） |
| `obd_scanner/ai_diag.py` | Claude API による診断（原因候補・確認手順・修理方法） |
| `obd_scanner/console.py` | COM4/5 の拒否、終了前に画面を止める処理 |
| `obd_scanner/simulator.py` | 動作確認用の疑似アダプタ |
| `tests/` | テスト（`python -m unittest discover -s tests`） |

繋がる設定がわかったら、`Elm327(...).connect(protocol="4", extra_init=["ATKW0"])` のように
その設定で接続でき、`obd_ai_diag.py` の通信部分をこれに置き換える予定です。
