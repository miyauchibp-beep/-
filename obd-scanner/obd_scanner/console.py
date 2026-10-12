"""Windows のコンソールから使うときの共通処理。"""

import os

# スナップオン（NANO-BT2）が使うポート。OBD 車検に関わるので絶対に触らない
FORBIDDEN_PORTS = {"COM4", "COM5"}


def ask_port(port):
    """ポート未指定なら聞く。使ってはいけないポートなら None を返す。"""
    if not port:
        port = input("アダプタの COM ポートを入力して Enter（何も入れなければ COM6）: ").strip() or "COM6"
    if port.upper() in FORBIDDEN_PORTS:
        print(f"{port} はスナップオン診断機（NANO-BT2）のポートなので使いません。")
        print("ELM327 アダプタのポート（COM6）を指定してください。")
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
