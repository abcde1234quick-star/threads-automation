"""
週次ジョブ（GitHub Actions用）
1. duckduckgo_search で美容トレンドをリサーチ
2. Claude API で15件の投稿を生成 → post_queue.md に追加
3. next-topics.md を更新
"""

import os
import re
import sys
import json
import datetime
from pathlib import Path

import anthropic
from duckduckgo_search import DDGS

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


def jst_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))


# ─── リサーチ ──────────────────────────────────────────────

def search_beauty_trends() -> str:
    """DuckDuckGoで日本の美容トレンドを検索（無料・APIキー不要）"""
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

    return "\n".join(results[:20])  # 上位20件


# ─── 知識ファイル読み込み ───────────────────────────────────

def load_knowledge() -> dict:
    files = {
        "profile": "01_profile.md",
        "target": "02_target.md",
        "genre": "03_genre.md",
        "writing": "05_writing.md",
        "references": "06_references.md",
        "ng_rules": "07_ng-rules.md",
    }
    data = {}
    for key, fname in files.items():
        path = KNOWLEDGE_DIR / fname
        data[key] = path.read_text(encoding="utf-8") if path.exists() else ""
    return data


def load_queue_ids() -> list[str]:
    """既存キューのIDを取得（重複防止）"""
    path = DATA_DIR / "post_queue.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return re.findall(r'id:\s*(\S+)', text)


def load_next_topics() -> str:
    path = DATA_DIR / "next-topics.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ─── 投稿生成 ──────────────────────────────────────────────

GENERATE_PROMPT = """あなたはThreads美容アカウント（@mao3.575）の投稿を生成するAIです。
以下のナレッジに基づいて、15件の投稿を生成してください。

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

各投稿のフォーマット:
```
===POST_START===
TYPE: TYPE_A
TOPIC: （テーマ）
BODY:
（投稿本文 200〜350字。1行目に数字か固有名詞を含む。改行多用。最後に質問。）
SELF_REPLY:
（セルフリプライ 100〜200字。補足・手順・豆知識。）
===POST_END===
```

ルール:
- 「〜です」「〜ます」は使わず、フラットな話し言葉で
- AI感のある表現（「〜ですね」「〜でしょう」「まとめると」）は禁止
- 断定禁止、仮説・体験として書く（「〜かもしれない」「気がした」）
- 同じTYPEを3件連続させない
"""


def generate_posts(knowledge: dict, research: str, existing_ids: list) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = GENERATE_PROMPT.format(
        profile=knowledge["profile"],
        target=knowledge["target"],
        genre=knowledge["genre"],
        writing=knowledge["writing"],
        ng_rules=knowledge["ng_rules"],
        research=research,
        next_topics=load_next_topics(),
        existing_ids=", ".join(existing_ids[-20:]) if existing_ids else "なし",
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ─── キューへの追加 ────────────────────────────────────────

def parse_generated(text: str) -> list[dict]:
    posts = []
    blocks = re.findall(r'===POST_START===(.*?)===POST_END===', text, re.DOTALL)
    for block in blocks:
        type_m = re.search(r'TYPE:\s*(\S+)', block)
        topic_m = re.search(r'TOPIC:\s*(.+)', block)
        body_m = re.search(r'BODY:\n(.*?)(?=SELF_REPLY:|===)', block, re.DOTALL)
        reply_m = re.search(r'SELF_REPLY:\n(.*?)$', block, re.DOTALL)

        if not (type_m and topic_m and body_m):
            continue

        posts.append({
            "type": type_m.group(1).strip(),
            "topic": topic_m.group(1).strip(),
            "body": body_m.group(1).strip(),
            "self_reply": reply_m.group(1).strip() if reply_m else "",
        })
    return posts


def append_to_queue(posts: list[dict]):
    today = jst_now().strftime("%Y-%m-%d")
    path = DATA_DIR / "post_queue.md"

    existing_ids = load_queue_ids()
    # 今日の最大連番を取得
    today_nums = [
        int(m.group(1))
        for eid in existing_ids
        if (m := re.search(rf'{today}-(\d+)', eid))
    ]
    next_num = max(today_nums, default=0) + 1

    entries = []
    for i, post in enumerate(posts):
        pid = f"{today}-{next_num + i:03d}"
        entry = f"""---
id: {pid}
type: {post['type']}
status: queued
source: weekly_job
topic: {post['topic']}
created: {today}
---

{post['body']}

"""
        if post["self_reply"]:
            entry += f"<!-- self_reply:\n{post['self_reply']}\n-->\n\n"
        entries.append(entry)

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(entries))

    print(f"[追加] {len(posts)}件をpost_queue.mdに追加")
    return len(posts)


# ─── メイン ───────────────────────────────────────────────

def main():
    print(f"=== weekly_job.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    # 1. リサーチ
    print("\n[1/3] 美容トレンドをリサーチ中...")
    research = search_beauty_trends()
    print(f"  取得: {len(research)}文字")

    # 2. 投稿生成
    print("\n[2/3] Claude APIで投稿生成中（15件）...")
    knowledge = load_knowledge()
    existing_ids = load_queue_ids()
    generated_text = generate_posts(knowledge, research, existing_ids)

    # 3. キューへ追加
    print("\n[3/3] post_queue.mdに追加中...")
    posts = parse_generated(generated_text)
    print(f"  パース成功: {len(posts)}件")

    if not posts:
        print("[ERROR] 投稿のパースに失敗しました。生成結果:")
        print(generated_text[:1000])
        sys.exit(1)

    count = append_to_queue(posts)

    # 現在のキュー残り件数を表示
    queue_text = (DATA_DIR / "post_queue.md").read_text(encoding="utf-8")
    queued = queue_text.count("status: queued")
    print(f"\n=== 完了 | 追加{count}件 | キュー残り{queued}件 ===")


if __name__ == "__main__":
    main()
