"""
キュー内の未投稿（status: queued）を 10 テンプレートで書き直す。
1件ずつ個別処理して確実に全件書き直す。
"""

import os
import re
import sys
import time
import anthropic
from pathlib import Path

from utils import jst_now, QUEUE_PATH, parse_queue, atomic_write
from config import TEMPLATES

if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

PERF_PATH = Path(__file__).parent.parent / "data" / "performance_summary.md"


def load_perf_guidance() -> str:
        """analyze_performance.py が生成した performance_summary.md からガイダンスを抽出"""
        if not PERF_PATH.exists():
                    return "（パフォーマンスデータなし）"
                text = PERF_PATH.read_text(encoding="utf-8")
    m = re.search(
                r"<!-- machine-readable-start -->(.*?)<!-- machine-readable-end -->",
                text, re.DOTALL
    )
    if not m:
                return "（パフォーマンスデータなし）"
            lines = {}
    for line in m.group(1).strip().splitlines():
                if ":" in line:
                                k, _, v = line.partition(":")
                                lines[k.strip()] = v.strip()
                        parts = []
    if lines.get("LOW_TEMPLATES"):
                parts.append(f"- 低スコアのため使用禁止テンプレート: {lines['LOW_TEMPLATES']}")
    if lines.get("BEST_TYPE"):
                parts.append(f"- 現在最も反応が良いタイプ: {lines['BEST_TYPE']}")
    if lines.get("TOP_TOPICS"):
                parts.append(f"- 高スコアトピック傾向（参考）: {lines['TOP_TOPICS']}")
    if lines.get("AVG_SCORE"):
                parts.append(f"- 現在の全体平均スコア: {lines['AVG_SCORE']}点")
    return "\n".join(parts) if parts else "（データ抽出失敗）"


# ── 1件書き直しプロンプト ──────────────────────────────────
SINGLE_PROMPT = """\
あなたはThreads美容アカウント（@mao3.575）の投稿ライターです。
以下のトピック1件について、最も合うテンプレートを選んで本文・セルフリプライを書いてください。

## 最新パフォーマンス指針（必ず守ること）
{perf_guidance}

## テンプレート一覧
{templates}

## 書き直し対象
トピック: {topic}
現在の本文: {body}

## 出力ルール
- 「〜です」「〜ます」禁止、フラットな話し言葉
- 「〜ですね」「〜でしょう」「まとめると」など AI感ある表現禁止
- 断定禁止、「〜かもしれない」「気がした」など仮説・体験として書く
- 文字数上限: TYPE_A→180字、TYPE_B→160字、TYPE_C→130字
- セルフリプライ: 40〜85字（本文の続きではなく新たな問いかけ）
- テンプレートの骨格を維持しつつトピックに合わせ具体的に埋める
- 低スコアテンプレートに指定されたものは絶対に使わない

## 出力フォーマット（このフォーマットのみ出力）
TEMPLATE: テンプレ番号（テンプレート名）
BODY:
（書き直した本文）
SELF_REPLY:
（書き直したセルフリプライ）
"""


def rewrite_one(post: dict) -> dict | None:
        perf_guidance = load_perf_guidance()
    prompt = SINGLE_PROMPT.format(
                perf_guidance=perf_guidance,
                templates=TEMPLATES,
                topic=post["topic"],
                body=post["body"][:300],
    )
    for attempt in range(3):
                try:
                                msg = client.messages.create(
                                                    model="claude-haiku-4-5-20251001",
                                                    max_tokens=2000,
                                                    messages=[{"role": "user", "content": prompt}],
                                )
                                raw        = msg.content[0].text
                                template_m = re.search(r"TEMPLATE:\s*(.+)", raw)
                                body_m     = re.search(r"BODY:\n(.*?)(?=SELF_REPLY:|\Z)", raw, re.DOTALL)
                                reply_m    = re.search(r"SELF_REPLY:\n(.*?)$", raw, re.DOTALL)
                                if not body_m:
                                                    print(f"    [retry {attempt+1}] BODY が見つかりません")
                                                    time.sleep(2)
                                                    continue
                                                return {
                                                                    "template":   template_m.group(1).strip() if template_m else "",
                                                                    "body":       body_m.group(1).strip(),
                                                                    "self_reply": reply_m.group(1).strip() if reply_m else "",
                                                }
except Exception as e:
            print(f"    [retry {attempt+1}] エラー: {e}")
            time.sleep(5)
    return None


def update_one_post(text: str, post: dict, rw: dict) -> str:
        new_topic = post["topic"].split(" [")[0]
    if rw["template"]:
                new_topic = f"{new_topic} [{rw['template']}]"

    new_body_block = rw["body"]
    if rw["self_reply"]:
                new_body_block += f"\n\n<!-- self_reply:\n{rw['self_reply']}\n-->"

    pid = re.escape(post["id"])
    block_pat = re.compile(
                r"---\n"
                r"id:\s*" + pid + r"\n"
                r"type:\s*(?P<type>[^\n]+)\n"
                r"status:\s*queued\n"
                r"source:\s*(?P<source>[^\n]+)\n"
                r"topic:\s*[^\n]+\n"
                r"(?:score_target:\s*[^\n]+\n)?"
                r"created:\s*(?P<created>[^\n]+)\n"
                r"---\n\n"
                r"(?P<body>.*?)(?=\n\n---|\Z)",
                re.DOTALL,
    )

    def replacer(m: re.Match) -> str:
                header = (
                                f"---\n"
                                f"id: {post['id']}\n"
                                f"type: {m.group('type').strip()}\n"
                                f"status: queued\n"
                                f"source: {m.group('source').strip()}\n"
                                f"topic: {new_topic}\n"
                                f"created: {m.group('created').strip()}\n"
                                f"---\n\n"
                )
        return header + new_body_block

    new_text, n = block_pat.subn(replacer, text, count=1)
    if n == 0:
                print(f"    [WARN] ID:{post['id']} パターン不一致、スキップ")
    return new_text


def main() -> None:
        print("=== rewrite_queue.py 開始 ===\n")

    text  = QUEUE_PATH.read_text(encoding="utf-8")
    posts = parse_queue(text)
    print(f"対象: {len(posts)}件の queued 投稿\n")

    if not posts:
                print("書き直し対象なし。終了。")
        return

    ok = ng = 0
    for i, post in enumerate(posts, 1):
                print(f"[{i:02d}/{len(posts)}] ID:{post['id']} | {post['topic'][:40]}...")
        rw = rewrite_one(post)
        if rw:
                        text = update_one_post(text, post, rw)
            print(f"         ✓ {rw['template']}")
            ok += 1
else:
            print(f"         ✗ 失敗")
            ng += 1
        time.sleep(1)

    atomic_write(QUEUE_PATH, text)
    print(f"\n=== 完了 | 成功:{ok}件 / 失敗:{ng}件 ===")


if __name__ == "__main__":
        main()
