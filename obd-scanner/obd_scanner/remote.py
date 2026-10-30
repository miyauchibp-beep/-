"""中継サーバー（server/）経由で診断・答え合わせを送る。

server.json（obd_scan.py と同じフォルダ）があればサーバー経由、なければこれまでどおり Claude API を直接使う。
    {"url": "https://obd.miyauchi-bp.com", "token": "add_user.php で表示された鍵"}
API キーは PC に置かなくてよくなり、診断と答え合わせはサーバーのデータベースに集まる。
"""

import json
import urllib.error
import urllib.request

from .ai_diag import AiDiagError, render_markdown

SERVER_FILE = "server.json"


def load_server(path=SERVER_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            server = json.load(f)
    except (OSError, ValueError):
        return None
    if not server.get("url") or not server.get("token"):
        return None
    return server


def _post(server, name, body, timeout):
    req = urllib.request.Request(
        server["url"].rstrip("/") + "/" + name,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + server["token"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read().decode("utf-8")).get("error", "")
        except (ValueError, UnicodeDecodeError):
            message = ""
        raise AiDiagError(f"中継サーバーでエラーが出ました（{e.code}）: {message or e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AiDiagError(f"中継サーバーに接続できません: {e}") from e


def remote_diagnose(server, report, vehicle="", symptom="", client="pc"):
    """サーバーに診断を頼む。diagnose() と同じ形 {"data", "text"} に、サーバーの番号 scan_id を加えて返す。"""
    body = {"report": {k: v for k, v in report.items() if k != "ai_diagnosis"},
            "vehicle": vehicle, "symptom": symptom, "client": client}
    res = _post(server, "diagnose.php", body, timeout=300)
    data = res.get("diagnosis")
    if not isinstance(data, dict):
        raise AiDiagError("中継サーバーから診断を受け取れませんでした")
    return {"data": data, "text": render_markdown(data), "scan_id": res.get("scan_id"),
            "past_cases": res.get("past_cases", 0)}


def remote_answer(server, scan_id, cause, work="", hit_rank=None, note=""):
    return _post(server, "answer.php", {"scan_id": scan_id, "cause": cause, "work": work,
                                        "hit_rank": hit_rank, "note": note}, timeout=30)
