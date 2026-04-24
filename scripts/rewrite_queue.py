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

# ─── 1件書き直しプロンプト ────────────────────────────────────
SINGLE_PROMPT = """\
あなたはThreads美容アカウント（@mao3.575）の投稿ライターです。
以下のトピック1件について、最も合うテンプレートを選んで本文・セルフリプライを書いてください。

## テンプレート一覧
{templates}

## 書き直し対象
トピック: {topic}
現在の本文: {body}

## 出力ルール
- 「〜です」「〜ます」禁止、フラットな話し言葉
- 「〜ですね」「〜でしょう」「まとめると」など AI感ある表現禁止
- 断定禁止、「〜かもしれない」「気がした」など仮説・体験として書く
- 本文: 200〜350字、改行多用
- セルフリプライ: 100〜200字（本文で言い切れなかった補足・手順・豆知識）
- テンプレートの骨格を維持しつつ○○をトピックに合わせ具体的に埋める

## 出力フォーマット（このフォーマットのみ出力）
TEMPLATE: テンプレ番号（テンプレート名）
BODY:
（書き直した本文）
SELF_REPLY:
（書き直したセルフリプライ）
"""


# ─── 1件書き直し ──────────────────────────────────────────────
def rewrite_one(post: dict) -> dict | None:
    prompt = SINGLE_PROMPT.format(
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


# ─── キューファイル更新 ────────────────────────────────────────
def update_one_post(text: str, post: dict, rw: dict) -> str:
    new_topic = post["topic"].split(" [")[0]  # 既存テンプレ表記を除去
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


# ─── メイン ───────────────────────────────────────────────────
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
