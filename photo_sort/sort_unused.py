# -*- coding: utf-8 -*-
"""
「未使用」の写真を元のフォルダ(車両ごとのフォルダ)ごとにまとめ、
枚数が多いものは「ブログ向け」、少ないものは「SNS向け」(インスタ・X・LINE)に分けます。

使い方:
    python3 sort_unused.py ~/ピクチャ/写真仕分け結果/未使用
オプション:
    --min 何枚以上をブログ向けにするか(初期値 8。ブログ記事の写真は中央値 15 枚)
    --out 仕分け先(省略時: 未使用フォルダの隣に「用途別」)
    --move コピーではなく移動する
"""
import argparse
import csv
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime

from PIL import Image

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def shot_time(path):
    """撮影日時(EXIF)。無ければファイルの更新日時"""
    try:
        with Image.open(path) as img:
            ex = img.getexif()
            v = ex.get_ifd(0x8769).get(36867) or ex.get(306)
            if v:
                return datetime.strptime(str(v).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return datetime.fromtimestamp(os.path.getmtime(path))


def main():
    ap = argparse.ArgumentParser(description="未使用の写真をブログ向け/SNS向けに仕分け")
    ap.add_argument("folder", help="「未使用」フォルダ")
    ap.add_argument("--min", type=int, default=8)
    ap.add_argument("--out")
    ap.add_argument("--move", action="store_true")
    args = ap.parse_args()

    src = os.path.abspath(args.folder)
    if not os.path.isdir(src):
        sys.exit(f"フォルダが見つかりません: {src}")
    out = os.path.abspath(args.out or os.path.join(os.path.dirname(src), "用途別"))
    if out.startswith(src + os.sep):
        sys.exit("仕分け先は未使用フォルダの外にしてください")

    # 元のフォルダごとにまとめる。フォルダに入っていない写真だけは撮影日ごと
    groups = defaultdict(list)  # まとまり名 -> [(時刻, パス)]
    for root, _, names in os.walk(src):
        rel = os.path.relpath(root, src)
        for n in names:
            if os.path.splitext(n)[1].lower() in IMAGE_EXT:
                p = os.path.join(root, n)
                t = shot_time(p)
                groups[rel if rel != "." else f"フォルダなし_{t:%Y-%m-%d}"].append((t, p))

    rows, blog, sns = [], 0, 0
    for label, items in sorted(groups.items()):
        kind = "ブログ向け" if len(items) >= args.min else "SNS向け"
        dst_dir = os.path.join(out, kind, label)
        os.makedirs(dst_dir, exist_ok=True)
        for t, p in sorted(items):
            (shutil.move if args.move else shutil.copy2)(p, os.path.join(dst_dir, os.path.basename(p)))
        times = sorted(t for t, _ in items)
        rows.append([kind, label, len(items), f"{times[0]:%Y-%m-%d}", f"{times[-1]:%Y-%m-%d}"])
        if kind == "ブログ向け":
            blog += 1
        else:
            sns += 1

    os.makedirs(out, exist_ok=True)
    report = os.path.join(out, "用途別一覧.csv")
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["用途", "フォルダ", "枚数", "最初の撮影日", "最後の撮影日"])
        w.writerows(rows)

    print("完了しました")
    print(f"  ブログ向け: {blog} 件   SNS向け: {sns} 件  ({args.min} 枚以上をブログ向け)")
    print(f"  仕分け先: {out}")
    print(f"  一覧表  : {report}")


if __name__ == "__main__":
    main()
