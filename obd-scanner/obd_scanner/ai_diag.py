"""読み取ったスキャン結果を Claude に渡し、原因候補・確認手順・修理方法を出させる。"""

import json

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """あなたは日本の自動車整備工場（板金・一般整備）で、整備士の診断を手伝うアシスタントです。
OBD2 スキャンツールで読み取ったデータと、車両情報・症状をもとに、整備士が次に何をすればよいかを日本語でまとめてください。

出力の形（Markdown）:
## 見立て
2〜3 行で要約。データだけで判断できないことは、そう書く。
## 原因候補（可能性の高い順）
それぞれ、根拠にしたデータ（故障コード・フリーズフレーム・ライブデータの値）と、可能性の目安（高・中・低）。
## 確認手順
安く早く確かめられるものから順に。測定箇所・基準値の目安・使う工具を具体的に。
## 修理方法
原因ごとの作業内容と、部品・工賃のおおまかな目安（地域・年式で変わる前提で）。
## 注意点
安全上の注意、関連して点検すべき箇所、再発防止など。

前提:
- 判断は候補であって断定ではない。最終判断は整備士が実車で確かめる。
- 読み取りは キー ON・エンジン停止 の場合もある。そのときの値（回転数 0、吸気管圧力≒大気圧、O2 センサ≒0V など）を異常と取り違えない。
- 「DTC 消去後の時間」「MIL 点灯中の時間」（PID 4E / 4D）は、キー ON・エンジン稼働中の時間を積算した値で、
  時計の経過時間ではない。「○時間前に消去」とは書かず、「消去後に合計○分（約○時間）稼働」と書く。
- stages があるときは、同じ車をキー ON（エンジン停止）で読んだあと、続けて始動して測ったもの。
  idle_after_start = 始動直後のアイドリング、warmup_log = 暖機中に 10 秒ごとに記録した水温・回転・燃料補正・O2、
  idle_warm = 暖機後のアイドリング、hold_2500rpm = 2,500rpm 前後で保持。各段階に live（ライブデータ）、
  trend（数秒間の動き）、pending_dtcs（その時点の保留中コード）がある。
  一番上の live・dtcs はキー ON・エンジン停止の値。段階を見比べて（例: 暖機で閉ループに入ったか、
  2,500rpm でリア O2 が安定するか、始動後に保留コードが出たか）総合的に判断する。
- engine_running はエンジン回転数から判定した稼働の有無。trend はエンジン稼働中に数秒間くり返し読んだ値で、
  最小・最大・平均と全サンプル（values）、フロント O2（PID 14）が 0.45V をまたいだ回数（switches）を含む。
  項目ごとの読み取り間隔（interval_sec）は Bluetooth 経由で 0.1〜0.5 秒ほどあり、速い反転は数え漏れるので、
  反転回数が少なくても、それだけで O2 センサの劣化と決めつけない。暖機前（水温が低い）の値であることも考慮する。
- 国内向けの車両（車台番号が 17 桁でない、メーカー独自コードなど）であることを考慮する。
- 車両情報が未入力のときは、車種・車格・排気量をデータから推測して書かない（吸入空気量などからの推測は外れやすい）。
- status.readiness_available が false の車は、ECU がレディネスを返さない（古い国内車でよくある）。
  「レディネスを確認してください」とは書かない。
- 「O2 センサの装着位置」（PID 13）があれば、どのセンサが付いているかはそれで判断する。
- 故障コードがなく症状もないときは、データから読み取れる点（バッテリー電圧、レディネス未完了、DTC 消去後の経過など）を短く伝えるだけでよい。
"""


def build_user_message(report, vehicle="", symptom=""):
    """スキャン結果と、整備士が入力した車両情報・症状を 1 つのメッセージにする。"""
    parts = []
    if vehicle:
        parts.append(f"■ 車両情報\n{vehicle}")
    parts.append(f"■ 症状・お客様の申告\n{symptom or '（入力なし）'}")
    parts.append("■ OBD2 スキャン結果（JSON）\n" + json.dumps(report, ensure_ascii=False, indent=2))
    return "\n\n".join(parts)


class AiDiagError(Exception):
    """Claude API を呼べなかったとき（API キー未設定・通信エラーなど）。"""


def diagnose(report, vehicle="", symptom="", model=MODEL, on_text=None, client=None):
    """Claude に診断させ、Markdown の本文を返す。on_text を渡すと、届いた文字から順に呼ぶ。"""
    try:
        import anthropic
    except ImportError as e:
        raise AiDiagError("anthropic が入っていません。`pip install anthropic` を実行してください") from e

    client = client or anthropic.Anthropic()
    try:
        # 安全上の判定で断られたときは、サーバー側で別のモデルに自動で切り替える
        with client.beta.messages.stream(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(report, vehicle, symptom)}],
        ) as stream:
            for text in stream.text_stream:
                if on_text:
                    on_text(text)
            message = stream.get_final_message()
    except TypeError as e:
        if "authentication" in str(e):
            raise AiDiagError(
                "Claude の API キーが設定されていません。環境変数 ANTHROPIC_API_KEY を設定してください"
            ) from e
        if "fallbacks" in str(e):
            raise AiDiagError(
                "anthropic が古いバージョンです。`pip install -U anthropic` で更新してください"
            ) from e
        raise
    except anthropic.AuthenticationError as e:
        raise AiDiagError("API キーが正しくありません。ANTHROPIC_API_KEY を確認してください") from e
    except anthropic.RateLimitError as e:
        raise AiDiagError("利用上限に達しました。しばらく待ってからやり直してください") from e
    except anthropic.APIConnectionError as e:
        raise AiDiagError("インターネットに接続できません。通信環境を確認してください") from e
    except anthropic.APIStatusError as e:
        raise AiDiagError(f"Claude API でエラーが出ました（{e.status_code}）: {e.message}") from e

    if message.stop_reason == "refusal":
        raise AiDiagError("Claude が回答を控えました。症状の書き方を変えてやり直してください")
    text = "".join(b.text for b in message.content if b.type == "text")
    if message.stop_reason == "max_tokens":
        text += "\n\n（回答が長すぎて途中で切れました）"
    return text
