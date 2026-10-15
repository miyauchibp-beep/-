# -*- coding: utf-8 -*-
"""
ホームページ(miyauchi-bp.com)に載せた写真と、PC の整備写真フォルダを照らし合わせ、
「使用済み」「未使用」に仕分けます。

ファイル名ではなく「画像の見た目」(知覚ハッシュ)で照合するので、
サイト側でファイル名が変わっていたり縮小されていても見つけられます。
撮影日時(EXIF)が一致する場合も「使用済み」と判定します。

使い方:
    py photo_sort.py "C:\\Users\\あなた\\Pictures\\整備写真"
オプション:
    --out  仕分け先フォルダ(省略時: 写真フォルダの隣に「写真仕分け結果」)
    --move コピーではなく移動する(元フォルダから写真が無くなります)
    --threshold 似ている判定の厳しさ(初期値 6。小さいほど厳しい)
    --refresh  サイトの写真一覧を取り直す(新しい記事を書いた後など)
"""
import argparse
import concurrent.futures as cf
import csv
import io
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime

import numpy as np
from PIL import Image, ImageOps

try:  # iPhone の HEIC 写真にも対応(pip install pillow-heif を入れた場合)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

SITE = "https://miyauchi-bp.com"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "site_hashes.json")
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
UA = {"User-Agent": "Mozilla/5.0 (photo_sort)"}


# ---------------------------------------------------------------- ハッシュ計算
def hashes(img):
    """(dHash, aHash) をそれぞれ 64bit 整数で返す"""
    g = img.convert("L")
    d = np.asarray(g.resize((9, 8), Image.LANCZOS), dtype=np.int16)
    dbits = (d[:, 1:] > d[:, :-1]).flatten()
    a = np.asarray(g.resize((8, 8), Image.LANCZOS), dtype=np.float32)
    abits = (a > a.mean()).flatten()
    to_int = lambda bits: int("".join("1" if b else "0" for b in bits), 2)
    return to_int(dbits), to_int(abits)


def exif_time(img):
    """撮影日時を 'YYYY:MM:DD HH:MM:SS' で返す(無ければ None)"""
    try:
        ex = img.getexif()
        v = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        return str(v).strip("\x00 ") if v else None
    except Exception:
        return None


def load_local(path):
    with Image.open(path) as img:
        t = exif_time(img)
        if img.format == "JPEG":
            img.draft("RGB", (400, 400))  # 大きい写真を速く読む
        img = ImageOps.exif_transpose(img)
        return hashes(img), t


# ---------------------------------------------------------------- サイト側
def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")), r.headers


def fetch_site_list():
    items, page = [], 1
    while True:
        url = (f"{SITE}/wp-json/wp/v2/media?per_page=100&page={page}"
               "&_fields=id,source_url,media_details")
        data, h = get_json(url)
        total_pages = int(h.get("X-WP-TotalPages", "1"))
        for m in data:
            md = m.get("media_details") or {}
            sizes = md.get("sizes") or {}
            small = (sizes.get("medium") or sizes.get("medium_large") or {}).get("source_url")
            ts = (md.get("image_meta") or {}).get("created_timestamp")
            try:
                ts = int(ts) if int(ts) > 0 else None
                ts = datetime.utcfromtimestamp(ts).strftime("%Y:%m:%d %H:%M:%S") if ts else None
            except (TypeError, ValueError, OverflowError, OSError):
                ts = None
            items.append({
                "id": m["id"],
                "url": m["source_url"],
                "hash_url": small or m["source_url"],
                "time": ts,
            })
        print(f"\r  サイトの写真一覧を取得中… {page}/{total_pages} ページ", end="", flush=True)
        if page >= total_pages:
            break
        page += 1
    print()
    return items


def hash_remote(item):
    try:
        req = urllib.request.Request(item["hash_url"], headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            img = Image.open(io.BytesIO(r.read()))
            img.load()
        d, a = hashes(img)
        return dict(item, d=format(d, "016x"), a=format(a, "016x"))
    except Exception:
        return None


def site_hashes(refresh):
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            cache = {c["id"]: c for c in json.load(f)}
        if not refresh:
            print(f"  サイトの写真 {len(cache)} 枚(保存済みデータを使用。--refresh で最新化)")
            return list(cache.values())
    items = [i for i in fetch_site_list() if i["id"] not in cache]
    print(f"  新しく調べるサイトの写真: {len(items)} 枚")
    done = 0
    with cf.ThreadPoolExecutor(8) as ex:
        for res in ex.map(hash_remote, items):
            done += 1
            if res:
                cache[res["id"]] = res
            if done % 50 == 0 or done == len(items):
                print(f"\r  サイトの写真を解析中… {done}/{len(items)}", end="", flush=True)
    print()
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(list(cache.values()), f, ensure_ascii=False)
    return list(cache.values())


# ---------------------------------------------------------------- 照合
POP8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def hamming(arr, h):
    x = np.bitwise_xor(arr, np.uint64(h))
    return POP8[x.view(np.uint8).reshape(-1, 8)].sum(axis=1)


def main():
    ap = argparse.ArgumentParser(description="ホームページ掲載写真と整備写真を照合して仕分け")
    ap.add_argument("folder", help="整備写真のフォルダ")
    ap.add_argument("--out", help="仕分け先フォルダ")
    ap.add_argument("--move", action="store_true", help="コピーではなく移動する")
    ap.add_argument("--threshold", type=int, default=6)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    src = os.path.abspath(args.folder)
    if not os.path.isdir(src):
        sys.exit(f"フォルダが見つかりません: {src}")
    out = os.path.abspath(args.out or os.path.join(os.path.dirname(src), "写真仕分け結果"))
    if out.startswith(src + os.sep):
        sys.exit("仕分け先は写真フォルダの外にしてください")

    print("1) ホームページの写真を確認")
    site = site_hashes(args.refresh)
    sd = np.array([int(s["d"], 16) for s in site], dtype=np.uint64)
    sa = np.array([int(s["a"], 16) for s in site], dtype=np.uint64)
    site_times = {}
    for s in site:
        if s.get("time"):
            site_times.setdefault(s["time"], s)

    print("2) PC の写真を照合")
    files = [os.path.join(r, n) for r, _, ns in os.walk(src) for n in ns
             if os.path.splitext(n)[1].lower() in IMAGE_EXT]
    print(f"  対象: {len(files)} 枚")
    rows, used, unused, errors = [], 0, 0, 0
    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, src)
        try:
            (d, a), t = load_local(path)
        except Exception as e:
            rows.append([rel, "読込エラー", "", "", str(e)])
            errors += 1
            continue
        dist = np.maximum(hamming(sd, d), hamming(sa, a))
        k = int(dist.argmin())
        best = int(dist[k])
        hit, how = None, ""
        if best <= args.threshold:
            hit, how = site[k], f"見た目一致(差 {best})"
        elif t and t in site_times and best <= args.threshold * 3:
            hit, how = site_times[t], "撮影日時一致"
        status = "使用済み" if hit else "未使用"
        used += bool(hit)
        unused += not hit
        rows.append([rel, status, hit["url"] if hit else "", how, ""])

        dst = os.path.join(out, status, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        (shutil.move if args.move else shutil.copy2)(path, dst)
        if i % 100 == 0 or i == len(files):
            print(f"\r  {i}/{len(files)} 枚  (使用済み {used} / 未使用 {unused})", end="", flush=True)
    print()

    os.makedirs(out, exist_ok=True)
    report = os.path.join(out, "仕分け一覧.csv")
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ファイル", "判定", "サイトの写真URL", "判定理由", "エラー"])
        w.writerows(rows)

    print("\n完了しました")
    print(f"  使用済み: {used} 枚   未使用: {unused} 枚   読込エラー: {errors} 枚")
    print(f"  仕分け先: {out}")
    print(f"  一覧表  : {report}")


if __name__ == "__main__":
    main()
