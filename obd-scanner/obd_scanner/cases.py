"""修理の結果（答え合わせ）を記録し、次の診断で参照できるようにする。

診断メモ（scan_logs/diag_日時.md）ごとに、実際の原因・作業・Claude の見立てが当たったかを
scan_logs/cases.jsonl に 1 行ずつ追記し、診断メモの末尾にも書き足す。
お客様の名前・電話番号は記録しない（Claude に送るため）。
"""

import glob
import json
import os
import re
from datetime import datetime

LOG_DIR = "scan_logs"
CASES_FILE = os.path.join(LOG_DIR, "cases.jsonl")

VERDICTS = {
    "1": "当たり（1 位の候補）",
    "2": "候補に入っていた（2 位以下）",
    "3": "外れ（候補になかった）",
}

ANSWER_HEADING = "## 答え合わせ"


def stamp_of(path):
    """diag_20261029_165618.md → 20261029_165618"""
    m = re.search(r"(\d{8}_\d{6})", os.path.basename(path))
    return m.group(1) if m else ""


def read_diag(md_path):
    """診断メモから車両・症状・読み取り結果のファイル名・答え合わせ済みかを取り出す。"""
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    info = {"path": md_path, "stamp": stamp_of(md_path), "vehicle": "", "symptom": "", "scan": "",
            "answered": ANSWER_HEADING in text}
    for key, label in (("vehicle", "車両"), ("symptom", "症状"), ("scan", "読み取り結果")):
        m = re.search(rf"^- {label}: (.*)$", text, re.M)
        if m and m.group(1) != "（未入力）":
            info[key] = m.group(1).strip()
    return info


def recent_diags(log_dir=LOG_DIR, limit=10):
    """新しい順に診断メモを返す。"""
    paths = sorted(glob.glob(os.path.join(log_dir, "diag_*.md")), reverse=True)
    return [read_diag(p) for p in paths[:limit]]


def scan_for(diag, log_dir=LOG_DIR):
    """診断メモに対応する読み取り結果（JSON）を読む。見つからなければ {}。"""
    names = [diag.get("scan"), f"scan_{diag['stamp']}.json"]
    for name in names:
        if not name:
            continue
        path = os.path.join(log_dir, os.path.basename(name))
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def all_codes(scan):
    """現在・保留中・消去不可の故障コードを重複なしで並べる。"""
    codes = []
    for group in (scan.get("dtcs") or {}).values():
        for d in group or []:
            if d.get("code") not in codes:
                codes.append(d.get("code"))
    return codes


def make_case(diag, scan, cause, work="", verdict="", note=""):
    return {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "diag": os.path.basename(diag["path"]),
        "vehicle": diag.get("vehicle", ""),
        "symptom": diag.get("symptom", ""),
        "calibration_id": scan.get("calibration_id") or "",
        "vin": scan.get("vin") or "",
        "dtcs": all_codes(scan),
        "cause": cause,
        "work": work,
        "verdict": VERDICTS.get(verdict, verdict),
        "note": note,
    }


def save_case(case, md_path=None, path=CASES_FILE):
    """cases.jsonl に追記し、診断メモの末尾にも書き足す。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")
    if md_path:
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(f"\n{ANSWER_HEADING}（{case['recorded_at'][:10]}）\n\n")
            f.write(f"- 実際の原因: {case['cause']}\n")
            if case["work"]:
                f.write(f"- 作業・交換部品: {case['work']}\n")
            if case["verdict"]:
                f.write(f"- Claude の見立て: {case['verdict']}\n")
            if case["note"]:
                f.write(f"- 決め手・メモ: {case['note']}\n")


def load_cases(path=CASES_FILE):
    cases = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        cases.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return cases


def similar_cases(report, cases=None, limit=5):
    """同じ車種の ECU（較正 ID が同じ）か、同じ車台番号の過去の答え合わせを、新しい順に返す。"""
    cases = load_cases() if cases is None else cases
    cal = report.get("calibration_id")
    vin = report.get("vin")
    hits = [c for c in cases
            if (cal and c.get("calibration_id") == cal) or (vin and c.get("vin") == vin)]
    keep = ("vehicle", "symptom", "dtcs", "cause", "work", "verdict", "note", "recorded_at")
    return [{k: c.get(k) for k in keep if c.get(k)} for c in reversed(hits)][:limit]


CSV_FILE = os.path.join(LOG_DIR, "cases.csv")
CSV_COLUMNS = [("recorded_at", "記録日"), ("vehicle", "車両"), ("symptom", "症状"), ("dtcs", "故障コード"),
               ("cause", "実際の原因"), ("work", "作業・交換部品"), ("verdict", "Claude の見立て"),
               ("note", "決め手・メモ"), ("calibration_id", "較正 ID"), ("diag", "診断メモ")]


def write_csv(cases=None, path=CSV_FILE):
    """答え合わせの一覧を Excel で開ける CSV（BOM 付き UTF-8）にする。"""
    import csv
    cases = load_cases() if cases is None else cases
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([label for _, label in CSV_COLUMNS])
        for c in cases:
            w.writerow([" ".join(c.get(k) or []) if k == "dtcs" else (c.get(k) or "")[:10] if k == "recorded_at"
                        else c.get(k, "") for k, _ in CSV_COLUMNS])
    return path


def hit_rate(cases=None):
    """(件数, 1 位で当たり, 候補に入っていた) を返す。"""
    cases = load_cases() if cases is None else cases
    judged = [c for c in cases if c.get("verdict") in VERDICTS.values()]
    top1 = sum(1 for c in judged if c["verdict"] == VERDICTS["1"])
    listed = sum(1 for c in judged if c["verdict"] in (VERDICTS["1"], VERDICTS["2"]))
    return len(judged), top1, listed
