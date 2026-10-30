"""中継サーバー用に、診断の指示文と JSON の形（スキーマ）を server/src/prompt.json に書き出す。

PC 版（obd_scanner/ai_diag.py）と同じものを使うため。ai_diag.py を直したら、これを実行してから
server/src/prompt.json をサーバーに上げる（テストが「古いまま」を見つける）。
    python make_server_prompt.py
"""

import json
import os

from obd_scanner.ai_diag import DIAGNOSIS_SCHEMA, MODEL, SYSTEM_PROMPT_JSON

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "server", "src", "prompt.json")


def build():
    return {"system": SYSTEM_PROMPT_JSON, "schema": DIAGNOSIS_SCHEMA, "model": MODEL}


def main():
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(build(), f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(PATH)


if __name__ == "__main__":
    main()
