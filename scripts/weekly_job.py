"""
週次ジョブ（GitHub Actions用）
1. DuckDuckGo で美容トレンドをリサーチ
2. 高スコア投稿をフィードバックとして取得
3. Claude API で 15 件の投稿を生成 → post_queue.md に追加
"""

import os
import re
import sys

import anthropic
from duckduckgo_search import DDGS

from utils import jst_now, DATA_DIR, KNOWLEDGE_DIR, HISTORY_PATH, QUEUE_PATH
from config import TEMPLATES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


# ─── リサーチ ──────────────────────────────────────────────────
def search_beauty_trends() -> str:
    queries = [
        "2026 スキンケア トレンド 日本 話題",
        "美容 成分 比較 効果 口コミ",
        "site:youtube.com 美容 スキンケア レビュー",
    ]
    results = []
    with DDGS() as ddgs:
        for q in queries:
            try:
                hits = list(ddgs.text(q, max_results=8))
                for h in hits:
                    results.append(f"【{h.get('title','')}】{h.get('body','')[:200]}")
            except Exception as e:
                print(f"  [WARN] 検索エラー: {e}")
    return "\n".join(results[:20])


# ─── 知識ファイル ──────────────────────────────────────────────
def load_knowledge() -> dict:
    files = {
        "profile":    "01_profile.md",
        "target":     "02_target.md",
        "genre":      "03_genre.md",
        "writing":    "05_writing.md",
        "references": "06_references.md",
        "ng_rules":   "07_ng-rules.md",
    }
    return {
        key: (KNOWLEDGE_DIR / fname).read_text(encoding="utf-8")
        if (KNOWLEDGE_DIR / fname).exists() else ""
        for key, fname in files.items()
    }


def load_queue_ids() -> list[str]:
    if not QUEUE_PATH.exists():
        return []
    return re.findall(r"id:\s*(\S+)", QUEUE_PATH.read_text(encoding="utf-8"))


def load_next_topics() -> str:
    path = DATA_DIR / "next-topics.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ─── フィードバックループ：高スコア投稿 ──────────────────────
def load_top_posts(n: int = 5) -> str:
    """スコア上位 N 件の投稿本文を返す（生成の参考に渡す）。"""
    if not HISTORY_PATH.exists():
        return ""
    text = HISTORY_PATH.read_text(encoding="utf-8")
    scored: list[dict] = []
    for block in re.split(r"\n---\n", text):
        score_m = re.search(r"\*\*score:\s*(\d+)\*\*", block)
        body_m  = re.search(r"### 本文\n(.*?)(?=\n###|\n---|\Z)", block, re.DOTALL)
        topic_m = re.search(r"- topic:\s*(.+)", block)
        if score_m and body_m:
            scored.append({
                "score": int(score_m.group(1)),
                "body":  body_m.group(1).strip()[:200],
                "topic": topic_m.group(1).strip() if topic_m else "",
            })
    if not scored:
        return "（まだ実績データなし）"
    scored.sort(key=lambda x: x["score"], reverse=True)
    lines = [f"## 高スコア実績投稿 TOP{n}（構造を参考にすること）"]
    for i, p in enumerate(scored[:n], 1):
        lines.append(f"\n### {i}位 score:{p['score']} | {p['topic'][:35]}")
        lines.append(p["body"])
    return "\n".join(lines)


# ─── 投稿生成 ──────────────────────────────────────────────────
GENERATE_PROMPT = """\
あなたはThreads美容アカウント（@mao3.575）の投稿を生成するAIです。
以下のナレッジとテンプレートに基づいて、15件の投稿を生成してください。

## アカウントプロフィール
{profile}

## ターゲット
{target}

## 投稿タイプ定義
{genre}

## 文章スタイル・構成
{writing}

## NG ルール
{ng_rules}

## 投稿テンプレート（必ずいずれか1つを選んで使う）
{templates}

## 高スコア実績投稿（これらの構造・トーンを参考に）
{top_posts}

## 今週のリサーチ結果（最新トレンド）
{research}

## 現在の次回テーマ候補
{next_topics}

## 既存キューID（重複禁止）
{existing_ids}

---

## 生成指示

以下のフォーマットで15件生成してください。
TYPE比率: A（比較）50% → 8件、B（検証）30% → 4件、C（共感）20% → 3件

テンプレート選択ルール:
- トピックの性質に最も合うテンプレートを1つ選ぶ
- 成分・使い方の比較 → テンプレ1・3・9
- 悩み系 → テンプレ2・5・10
- コスパ・プチプラ系 → テンプレ6・8
- NG習慣系 → テンプレ7
- 継続・体験報告系 → テンプレ4
- 同じテンプレートを連続3件以上使わない

各投稿のフォーマット:
===POST_START===
TYPE: TYPE_A
TEMPLATE: テンプレ○（テンプレート名）
TOPIC: （テーマ）
BODY:
（テンプレートの構造に従った投稿本文 200〜350字。改行多用。）
SELF_REPLY:
（セルフリプライ 100〜200字。本文では言い切れなかった補足・手順・豆知識。）
===POST_END===

ルール:
- 「〜です」「〜ます」は使わず、フラットな話し言葉
- AI感のある表現（「〜ですね」「〜でしょう」「まとめると」）は禁止
- 断定禁止、仮説・体験として書く（「〜かもしれない」「気がした」）
- 同じTYPEを3件連続させない
- テンプレートの骨格を維持しつつ○○をトピックに合わせ具体的に埋める
"""


def generate_posts(knowledge: dict, research: str, top_posts: str, existing_ids: list) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = GENERATE_PROMPT.format(
        profile=knowledge["profile"],
        target=knowledge["target"],
        genre=knowledge["genre"],
        writing=knowledge["writing"],
        ng_rules=knowledge["ng_rules"],
        templates=TEMPLATES,
        top_posts=top_posts,
        research=research,
        next_topics=load_next_topics(),
        existing_ids=", ".join(existing_ids[-20:]) if existing_ids else "なし",
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ─── パース & キュー追記 ──────────────────────────────────────
def parse_generated(text: str) -> list[dict]:
    posts = []
    for block in re.findall(r"===POST_START===(.*?)===POST_END===", text, re.DOTALL):
        type_m     = re.search(r"TYPE:\s*(\S+)", block)
        template_m = re.search(r"TEMPLATE:\s*(.+)", block)
        topic_m    = re.search(r"TOPIC:\s*(.+)", block)
        body_m     = re.search(r"BODY:\n(.*?)(?=SELF_REPLY:|===|\Z)", block, re.DOTALL)
        reply_m    = re.search(r"SELF_REPLY:\n(.*?)$", block, re.DOTALL)

        if not (type_m and topic_m and body_m):
            print(f"  [WARN] パース失敗ブロック: {block[:60]!r}")
            continue

        topic    = topic_m.group(1).strip()
        template = template_m.group(1).strip() if template_m else ""
        posts.append({
            "type":       type_m.group(1).strip(),
            "topic":      f"{topic} [{template}]" if template else topic,
            "body":       body_m.group(1).strip(),
            "self_reply": reply_m.group(1).strip() if reply_m else "",
        })
    return posts


def append_to_queue(posts: list[dict]) -> int:
    today = jst_now().strftime("%Y-%m-%d")
    existing_ids = load_queue_ids()
    today_nums = [
        int(m.group(1))
        for eid in existing_ids
        if (m := re.search(rf"{today}-(\d+)", eid))
    ]
    next_num = max(today_nums, default=0) + 1

    entries = []
    for i, post in enumerate(posts):
        pid   = f"{today}-{next_num + i:03d}"
        entry = (
            f"---\n"
            f"id: {pid}\n"
            f"type: {post['type']}\n"
            f"status: queued\n"
            f"source: weekly_job\n"
            f"topic: {post['topic']}\n"
            f"created: {today}\n"
            f"---\n\n"
            f"{post['body']}\n\n"
        )
        if post["self_reply"]:
            entry += f"<!-- self_reply:\n{post['self_reply']}\n-->\n\n"
        entries.append(entry)

    with open(QUEUE_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(entries))

    print(f"[追加] {len(posts)}件を post_queue.md に追加")
    return len(posts)


# ─── メイン ───────────────────────────────────────────────────
def main() -> None:
    print(f"=== weekly_job.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    # 1. リサーチ
    print("\n[1/4] 美容トレンドをリサーチ中...")
    research = search_beauty_trends()
    print(f"  取得: {len(research)}文字")

    # 2. フィードバックループ：高スコア投稿を取得
    print("\n[2/4] 高スコア実績投稿を取得中...")
    top_posts = load_top_posts(n=5)
    print(f"  取得完了")

    # 3. 投稿生成
    print("\n[3/4] Claude API で投稿生成中（15件）...")
    knowledge    = load_knowledge()
    existing_ids = load_queue_ids()
    generated    = generate_posts(knowledge, research, top_posts, existing_ids)

    # 4. キューへ追加
    print("\n[4/4] post_queue.md に追加中...")
    posts = parse_generated(generated)
    print(f"  パース成功: {len(posts)}件")

    if not posts:
        print("[ERROR] 投稿のパースに失敗。生成結果:")
        print(generated[:1000])
        sys.exit(1)

    count  = append_to_queue(posts)
    queued = QUEUE_PATH.read_text(encoding="utf-8").count("status: queued")
    print(f"\n=== 完了 | 追加{count}件 | キュー残り{queued}件 ===")


if __name__ == "__main__":
    main()
