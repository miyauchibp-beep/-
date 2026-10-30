# OBD 診断の中継サーバー（obd.miyauchi-bp.com）

アプリ・PC 版から受け取ったスキャン結果で Claude に診断させ、診断と答え合わせを MySQL に保存する。
Claude の API キーはこのサーバーの `config.php` にだけ置く（アプリ・PC には置かない、GitHub にも入れない）。
車にコマンドを送ることはない。

| 置き場所 | 中身 |
| --- | --- |
| `public/` | 外から見える入口（ドメインのドキュメントルート） `diagnose.php`・`answer.php`・`health.php` |
| `src/` | 本体（`app.php`）と、PC 版と同じ指示文・スキーマ（`prompt.json`） |
| `vendor/` | Claude の公式 PHP 部品（`anthropic-ai/sdk`）と通信部品（`guzzlehttp/guzzle`） |
| `config.php` | API キー・データベースのパスワード（**サーバーの中だけ**。`config.sample.php` から作る） |
| `schema.sql` | データベースの表（利用者・スキャンと診断・答え合わせ） |
| `bin/add_user.php` | 利用者（整備士・お店）を追加して鍵を発行する |

## 置き方（mixhost、cPanel）

1. **ファイルを上げる**: `dist/obd_server.zip` を cPanel の「ファイルマネージャー」でホーム（`/home/jytgvshy/`）に
   アップロードして展開する → `/home/jytgvshy/obd-relay/` ができる（`public_html` の外に置く）
2. **サブドメイン**: 「ドメイン」→「新しいドメインを作成」で `obd.miyauchi-bp.com` を作り、
   ドキュメントルートを `/home/jytgvshy/obd-relay/public` にする。PHP は 8.3（「MultiPHP Manager」）
3. **データベース**: 「Database Wizard」で `jytgvshy_obd` と `jytgvshy_obd_app` を作り、すべての権限を付ける。
   phpMyAdmin で `jytgvshy_obd` を選び、「インポート」で `schema.sql` を読み込む
4. **設定**: ターミナルで
   ```
   cd ~/obd-relay
   cp config.sample.php config.php
   chmod 600 config.php
   nano config.php        # API キーと DB のパスワードを書く（ここだけ自分で入力）
   ```
5. **利用者を追加**: `php bin/add_user.php "宮内自動車" 30` → 表示された鍵を控える（1 回しか出ない）
6. **確認**: ブラウザで `https://obd.miyauchi-bp.com/health.php` を開いて `{"ok":true}` なら動いている。
   SSL（https）は cPanel の「SSL/TLS Status」で自動の証明書が付いているか見る

## PC 版から使う

`obd_scan.py` と同じフォルダに `server.json` を置くと、診断はサーバー経由になる（PC に API キーはいらない）。
```
{"url": "https://obd.miyauchi-bp.com", "token": "add_user.php で表示された鍵"}
```
答え合わせ（`run_result.bat`）も、サーバー経由の診断ならサーバーに送られる。

## 約束（アプリもこの形で送る）

- `POST /diagnose.php`、`Authorization: Bearer <鍵>`、本文 `{"report": スキャン結果, "vehicle", "symptom", "client": "pc" か "android"}`
  → `{"scan_id", "diagnosis": 決まった形の診断, "past_cases": 渡した事例の数}`
- `POST /answer.php`、本文 `{"scan_id", "cause", "work", "hit_rank": 当たった候補の順位（0 = どれでもない）, "note"}`
- エラーは `{"error": "日本語の説明"}`（401 鍵、429 1 日の上限、502 Claude 側）

## 決まり

- 車台番号は下 4 桁だけ（送られてきても、サーバーで伏せてから Claude に送り、保存する）。お客様の名前・電話番号は持たない
- 1 人 1 日の診断回数に上限（`users.daily_limit`）。Claude Console の月の利用上限も設定しておく
- 同じ較正 ID の答え合わせ（仲間の分も含む、新しい順に 5 件）を診断のときに Claude に渡す
- 指示文とスキーマは PC 版と同じ（`python make_server_prompt.py` で `src/prompt.json` を作り直す）

## テスト

`php tests/test_server.php`（データベースは SQLite のメモリ上、Claude は偽物）
