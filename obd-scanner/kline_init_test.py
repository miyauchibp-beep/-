"""K-line（KWP / ISO 9141）で ECU に繋がる初期化設定を、順番に試して探す。

使い方（Windows）:
    python kline_init_test.py COM6
    python kline_init_test.py COM6 --all      # 成功しても最後まで全部試す
    python kline_init_test.py COM6 --round 1  # 1 回目（一般的な設定）をもう一度
    python kline_init_test.py --demo          # アダプタなしで動作確認

車両に送るのは読み取りの要求（01 / 03 / 1A / 21）だけです。
故障コードの消去や書き込みは一切しません。

結果:
    kline_logs/kline_test_YYYYMMDD_HHMMSS.txt  … 送受信の全記録
    working_init.json                          … 繋がった設定（成功したとき）
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    from obd_scanner.elm327 import PROTOCOLS, Elm327, ObdError, parse_response
    from obd_scanner.pids import decode_monitor_status, supported_from_bitmap
except ImportError:
    # ダブルクリック起動だと、ここで落ちると画面が一瞬で閉じてしまうので待つ
    print("obd_scanner フォルダが見つかりません。")
    print("kline_init_test.py と同じ場所に、obd_scanner フォルダごとコピーしてください。")
    print(f"  今の場所: {os.path.dirname(os.path.abspath(__file__))}")
    if os.name == "nt":
        input("\nEnter キーを押すと閉じます...")
    sys.exit(1)

# スナップオン（NANO-BT2）が使うポート。OBD 車検に関わるので絶対に触らない
FORBIDDEN_PORTS = {"COM4", "COM5"}

# (プロトコル, 追加コマンド, 説明)
# 1 回目: 一般的な設定。2026-10-12 のアレックスでは全滅（5baud の呼びかけに返事なし）
ROUND1 = [
    ("4", [], "基本: KWP 5baud初期化"),
    ("4", ["ATKW0"], "キーワード検査オフ"),
    ("4", ["ATKW0", "ATAT0", "ATSTFF"], "キーワード検査オフ + 応答待ちを最大に"),
    ("4", ["ATKW0", "ATIIA33", "ATIB10"], "キーワード検査オフ + 初期化アドレス33・10400bps明示"),
    ("4", ["ATKW0", "ATSH8110F1"], "キーワード検査オフ + エンジンECU(10)を直接指定"),
    ("5", [], "KWP 高速初期化"),
    ("5", ["ATKW0"], "KWP 高速初期化 + キーワード検査オフ"),
    ("3", [], "ISO 9141-2"),
    ("3", ["ATKW0"], "ISO 9141-2 + キーワード検査オフ"),
    ("0", [], "自動判別"),
]

# 2 回目: 呼びかける宛先（初期化アドレス）を変える。
# 共通規格の 33 には返事がなかったので、国内向けの ECU が使いそうな宛先を順に試す
_INIT_ADDRESSES = ["01", "10", "11", "12", "13", "15", "17", "18", "1A", "28", "58", "02"]
ROUND2 = [("4", ["ATKW0", f"ATIIA{a}"], f"5baud初期化 宛先 {a}") for a in _INIT_ADDRESSES] + [
    ("5", ["ATKW0", f"ATSH81{a}F1"], f"高速初期化 宛先 {a}") for a in ["10", "11", "01"]
]

ROUNDS = {1: ROUND1, 2: ROUND2}

# 初期化に成功したあとに送る読み取り要求（すべて読み取りのみ）
#   01 00/01 01 … 共通規格の対応PID・警告灯   03 … 故障コード
#   1A 80/1A 9B … KWP の ECU 情報読み取り      21 01 … メーカー独自データの読み取り
PROBES = ["0100", "0101", "03", "1A80", "1A9B", "2101"]


class Logger:
    def __init__(self, path):
        self.path = path
        self._f = open(path, "w", encoding="utf-8")

    def write(self, text):
        self._f.write(text + "\n")
        self._f.flush()

    def close(self):
        self._f.close()


def exchange(elm, log, cmd, timeout=None):
    """コマンドを送り、ログに残して応答文字列（またはエラー）を返す。"""
    try:
        reply = elm.send_raw(cmd, timeout=timeout)
    except ObdError as e:
        reply = f"(応答なし: {e})"
    shown = " | ".join(x.strip() for x in reply.replace(">", "").splitlines() if x.strip())
    log.write(f"  > {cmd:<12} {shown}")
    return reply


def try_variant(elm, log, protocol, extra, wait):
    """1 つの設定で初期化を試す。

    返り値: "ok"   … 共通規格（01 00）に答えた
            "init" … 初期化は成功したが、01 00 には答えなかった（国内独自の可能性）
            None   … 初期化に失敗
    """
    exchange(elm, log, "ATPC", timeout=3)
    if wait:
        time.sleep(wait)  # K-line は前回の初期化失敗から数秒あけないと受け付けないことがある
    exchange(elm, log, "ATZ", timeout=5)
    for cmd in ("ATE0", "ATL0", "ATS1", "ATH0"):
        exchange(elm, log, cmd, timeout=3)
    exchange(elm, log, f"ATSP{protocol}", timeout=3)
    for cmd in extra:
        r = exchange(elm, log, cmd, timeout=3)
        if "?" in r:
            log.write(f"  ! {cmd} はこのアダプタで未対応")
    reply = exchange(elm, log, "0100", timeout=20)
    try:
        messages = parse_response(reply)
    except ObdError:
        return None
    if any(m[:2] == [0x41, 0x00] for m in messages):
        return "ok"
    upper = reply.upper()
    if ("BUS INIT" in upper and "OK" in upper) or messages:
        return "init"
    return None


def run_probes(elm, log):
    """初期化できたあと、読み取り要求を順に送って、答えたものを返す。"""
    answered = []
    for cmd in PROBES:
        reply = exchange(elm, log, cmd, timeout=5)
        try:
            messages = parse_response(reply)
        except ObdError:
            continue
        if messages:
            answered.append((cmd, " | ".join(" ".join(f"{b:02X}" for b in m) for m in messages)))
    return answered


def summarize_success(elm, log):
    """繋がったあと、プロトコル名と MIL・故障コード件数を読む。"""
    dpn = exchange(elm, log, "ATDPN", timeout=3).strip(" \r\n>")
    elm.protocol = dpn.lstrip("A")
    lines = [f"プロトコル番号: {dpn}"]
    try:
        bodies = elm.query(0x01, 0x00)
        if bodies:
            lines.append(f"対応PID(01-20): {len(supported_from_bitmap(0, bodies[0]))} 個")
        status = elm.query(0x01, 0x01)
        if status and len(status[0]) >= 4:
            s = decode_monitor_status(status[0])
            lines.append(f"警告灯(MIL): {'点灯' if s['mil'] else '消灯'} / 故障コード件数: {s['dtc_count']}")
    except ObdError as e:
        lines.append(f"(追加の読み取りで失敗: {e})")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="K-line の初期化設定を順番に試す")
    ap.add_argument("port", nargs="?", help="アダプタの COM ポート（例: COM6）")
    ap.add_argument("--baudrate", type=int, default=38400)
    ap.add_argument("--wait", type=float, default=5.0, help="設定の切り替え前に待つ秒数（既定5）")
    ap.add_argument("--all", action="store_true", help="成功しても全部試す")
    ap.add_argument("--only", type=int, nargs="+", help="試す番号だけ指定（例: --only 2 3）")
    ap.add_argument("--round", type=int, choices=sorted(ROUNDS), default=2,
                    help="試す設定の組（1: 一般的な設定、2: 宛先を変える。既定 2）")
    ap.add_argument("--demo", action="store_true", help="アダプタなしでシミュレータに対して実行")
    args = ap.parse_args(argv)

    if args.demo:
        from obd_scanner.simulator import FakeElm327
        # 1 回目: 宛先 33 でキーワード検査が引っかかる車 / 2 回目: 宛先 10 の国内独自の車
        fake = FakeElm327() if args.round == 1 else FakeElm327(init_address="10", generic_obd=False)
        port, wait = fake, 0
    else:
        if not args.port:
            # ダブルクリックで起動したときはポートを聞く
            args.port = input("アダプタの COM ポートを入力して Enter（何も入れなければ COM6）: ").strip() or "COM6"
        if args.port.upper() in FORBIDDEN_PORTS:
            print(f"{args.port} はスナップオン診断機（NANO-BT2）のポートなので使いません。")
            print("ELM327 アダプタのポート（COM6）を指定してください。")
            return 2
        port, wait = args.port, args.wait

    os.makedirs("kline_logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Logger(os.path.join("kline_logs", f"kline_test_{stamp}.txt"))
    log.write(f"K-line 初期化テスト {datetime.now():%Y-%m-%d %H:%M:%S}  ポート: {args.port or 'デモ'}  第{args.round}回の設定")

    try:
        elm = Elm327(port, baudrate=args.baudrate)
    except ObdError as e:
        print(f"接続できません: {e}")
        print("・スマホの Bluetooth をオフ / Car Scanner を閉じましたか？")
        print("・アダプタのボタンは ON ですか？")
        return 1

    print("アダプタを確認しています...")
    log.write("\n[アダプタ確認]")
    version = exchange(elm, log, "ATZ", timeout=5).strip(" \r\n>")
    if "ELM" not in version.upper():
        # 接続直後はゴミが混じって ATZ の応答が崩れることがあるので、もう一度だけ聞く
        time.sleep(1)
        version = exchange(elm, log, "ATI", timeout=5).strip(" \r\n>")
    if "ELM" not in version.upper():
        print(f"アダプタから正しい応答がありません: {version!r}")
        log.close()
        return 1
    volt = exchange(elm, log, "ATRV", timeout=3).strip(" \r\n>")
    print(f"  アダプタ: {version.splitlines()[-1].strip()}  電圧: {volt}")
    try:
        if float(volt.rstrip("Vv")) < 12.2:
            print("  ※ 電圧が低めです。安定化電源をつなぐか、エンジンをかけて試すと結果が変わることがあります。")
    except ValueError:
        pass

    variants = list(enumerate(ROUNDS[args.round], 1))
    if args.only:
        variants = [v for v in variants if v[0] in args.only]

    found = []
    print(f"\n{len(variants)} 通りの設定を試します（1つあたり {wait + 5:.0f}〜{wait + 25:.0f} 秒）。キーは ON のままにしてください。\n")
    for no, (protocol, extra, label) in variants:
        print(f"[{no:>2}] {label} ... ", end="", flush=True)
        log.write(f"\n[{no}] プロトコル{protocol} {' '.join(extra) or '(追加なし)'}  {label}")
        status = try_variant(elm, log, protocol, extra, wait)
        if status == "ok":
            print("成功")
            details = summarize_success(elm, log)
            for line in details:
                print(f"       {line}")
            log.write("  => 成功: " + " / ".join(details))
            # 自動判別（0）のときは、実際に見つかったプロトコル番号を残す
            detected = elm.protocol if protocol == "0" and elm.protocol else protocol
            found.append({"no": no, "protocol": detected, "extra_init": extra, "label": label,
                          "generic_obd": True})
            if not args.all:
                break
        elif status == "init":
            print("初期化は成功（共通規格の質問には無回答）")
            log.write("  => 初期化成功。読み取り要求を試す")
            answered = run_probes(elm, log)
            for cmd, data in answered:
                print(f"       {cmd} → {data}")
            if not answered:
                print("       どの読み取り要求にも返事なし")
            found.append({"no": no, "protocol": protocol, "extra_init": extra, "label": label,
                          "generic_obd": False, "answered": answered})
            if not args.all:
                break
        else:
            print("失敗")
            log.write("  => 失敗")

    exchange(elm, log, "ATPC", timeout=3)
    elm.close()
    log.close()

    print()
    if found:
        best = found[0]
        result = {
            "port": args.port,
            "protocol": best["protocol"],
            "extra_init": best["extra_init"],
            "label": best["label"],
            "generic_obd": best["generic_obd"],
            "found_at": datetime.now().isoformat(timespec="seconds"),
            "all_success": found,
        }
        if not args.demo:
            with open("working_init.json", "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print("繋がった設定を working_init.json に保存しました。")
        cmds = " ".join(best["extra_init"]) or "(追加なし)"
        name = PROTOCOLS.get(best["protocol"], "")
        print(f"→ プロトコル {best['protocol']} {name} / 追加コマンド: {cmds}")
    else:
        print("どの設定でも繋がりませんでした。")
        print("次の手:")
        print("  1. Car Scanner の通信ログ・接続設定を見て、送っているコマンドを比べる")
        print("  2. 安定化電源で 13.5V 前後にする、またはエンジンをかけてから再実行")
        if args.round == 2:
            print("  （1 回目の設定をもう一度試すなら: python kline_init_test.py COM6 --round 1）")
    print(f"記録: {log.path}")
    return 0 if found else 1


def run_from_console():
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


if __name__ == "__main__":
    sys.exit(run_from_console())
