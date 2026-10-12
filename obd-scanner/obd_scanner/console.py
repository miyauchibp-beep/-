"""Windows のコンソールから使うときの共通処理。"""

import os

# スナップオン（NANO-BT2）が使うポート。OBD 車検に関わるので絶対に触らない
FORBIDDEN_PORTS = {"COM4", "COM5"}

# 前回つながったポートを覚えておくファイル（ペアリングし直すと番号が変わるため）
PORT_FILE = "obd_port.txt"
DEFAULT_PORT = "COM7"  # 2026-10-12 時点の OBDII「発信」ポート


def last_port():
    try:
        with open(PORT_FILE, encoding="utf-8") as f:
            return f.read().strip() or DEFAULT_PORT
    except OSError:
        return DEFAULT_PORT


def remember_port(port):
    try:
        with open(PORT_FILE, "w", encoding="utf-8") as f:
            f.write(port)
    except OSError:
        pass


def ask_port(port):
    """ポート未指定なら聞く。使ってはいけないポートなら None を返す。"""
    if not port:
        default = last_port()
        print("アダプタのポートは、Windows の「その他の Bluetooth 設定」→「COM ポート」で")
        print("「OBDII」の「発信」になっている番号です。")
        port = input(f"COM ポートを入力して Enter（何も入れなければ {default}）: ").strip() or default
    if port.isdigit():
        port = "COM" + port
    elif port.upper().startswith("COM"):
        port = port.upper()
    if port in FORBIDDEN_PORTS:
        print(f"{port} はスナップオン診断機（NANO-BT2）のポートなので使いません。")
        print("「OBDII」の「発信」になっている番号を指定してください。")
        return None
    return port


def run_from_console(main):
    """エラーでも画面がすぐ閉じないよう、最後に Enter を待つ。"""
    code = 1
    try:
        code = main()
    except KeyboardInterrupt:
        print("\n中断しました。")
    except Exception:
        import traceback
        print("\n予期しないエラーが出ました。この画面の文字をすべてコピーして Claude に送ってください。\n")
        traceback.print_exc()
    if os.name == "nt":
        input("\nEnter キーを押すと閉じます...")
    return code
