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

TEMPLATES = """
## 投稿テンプレート10種（バズ実績あり）

各テンプレートの構造を厳守して本文を作ること。

---
【テンプレ1】「やりすぎNG」逆張り型（驚き・比較）
実は【○○】やりすぎると逆効果だった…
✗ やってた：毎日○○
✓ 正解：○○は週○回まで
この差で肌の○○が全然違う
知らなかった人はぜひ試してみて🙏
#スキンケア　#美容　#やりすぎNG
→ 冒頭に「実は…」「逆効果だった」など裏切りワードで停止率UP

---
【テンプレ2】「わかる！」共感爆発型（共感・悩み）
○○に悩んでる人、絶対共感するやつ↓
「○○してるのになんか違う…」
「なんか老けて見える気がする」
「○○試したけど続かない」
全部あてはまってた私が唯一続いたのが○○です
#美容悩み　#スキンケア難民　#共感
→ 悩みの言語化→解決の流れでリプライが伸びやすい

---
【テンプレ3】「知らなかった」情報差型（教育・知識）
美容部員に聞いたら教えてもらえた話
○○の正しい使い方、みんなに知ってほしいんだけど
①○○
②○○
③○○
これだけで○○が変わります
保存しておいて損なし📎
#美容知識　#コスメ　#保存必須
→「プロから聞いた」という権威性で信頼感UP

---
【テンプレ4】「before/after」変化実感型（驚き・比較）
○○を始めて○ヶ月。正直に報告します
【1ヶ月目】○○
【2ヶ月目】○○
【3ヶ月目】○○←ここで変化を実感
使ったのは○○だけ。コスパも◎
続けてよかった…🥺
#美容記録　#ビフォーアフター　#スキンケア
→「正直に報告」の誠実さで読者の信頼を獲得

---
【テンプレ5】「あなたのタイプは？」診断型（共感・悩み）
肌タイプ別・おすすめ○○まとめました
🔵 乾燥肌さんには→○○
🟡 混合肌さんには→○○
🔴 敏感肌さんには→○○
⚪ 普通肌さんには→○○
自分のタイプはどれ？コメントで教えて！
#肌タイプ　#スキンケア診断　#コメント歓迎
→ コメント誘発でエンゲージメント急増

---
【テンプレ6】「コスパ最強」プチプラ暴露型（行動誘発）
デパコス愛用者の私が言う「プチプラで十分」なアイテム3選
①○○（¥○○）→○○と同じ使い心地
②○○（¥○○）→○○の代わりに
③○○（¥○○）→むしろこっちの方が好き
お金は大事なところに使おう🤍
#プチプラコスメ　#コスパ最強　#節約美容
→「デパコス愛用者が言う」でブランド信頼を担保

---
【テンプレ7】「今すぐやめて」警告型（行動誘発）
悪化するだけだから今すぐやめて🚨
✗ ○○しながらスキンケア
✗ ○○の順番が逆
✗ 寝る前に○○を使う
これ全部、肌への負担が大きい
知らずにやってた人は今日から変えてみて
#スキンケアNG　#美容失敗　#肌荒れ
→「今すぐやめて」の緊急性ワードで即スクロール停止

---
【テンプレ8】「○○円以下縛り」挑戦型（行動誘発）
スキンケア全部○○円以下に統一してみた話
洗顔：○○（¥○○）
化粧水：○○（¥○○）
乳液：○○（¥○○）
UVケア：○○（¥○○）
合計○○円で肌の調子がむしろよくなった件
全部ドラッグストアで買える🙋
#プチプラスキンケア　#○○円以下　#ドラッグストアコスメ
→ 具体的な金額制限がリアリティを生みシェアされやすい

---
【テンプレ9】「この成分だけ見て」成分解説型（教育・知識）
コスメ買うとき成分表の「○○」だけ見てれば正直OK
◎ ○○が入ってる→買い
△ ○○のみ→普通
✗ ○○がある→○○肌は避けて
難しく考えなくていい
これだけ覚えれば失敗しなくなるよ📝
#成分解説　#コスメ選び　#美容成分
→ ◎△✗の評価軸で読者が即自分のコスメに当てはめられる

---
【テンプレ10】「○代の私へ」タイムスリップ型（共感・悩み）
20代の自分に教えてあげたいこと（美容編）
「○○はそんなにいらない」
「○○だけやれば十分だった」
「○○に早く出会いたかった」
今の○○代の子たちに届いてほしい🙏
これだけでホントに変わるから
#美容後悔　#20代必見　#スキンケア
→「届いてほしい」という利他的な言葉がシェアを促進
"""

GENERATE_PROMPT = """あなたはThreads美容アカウント（@mao3.575）の投稿を生成するAIです。
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

**テンプレート選択ルール:**
- トピックの性質に最も合うテンプレートを1つ選ぶ
- 成分・使い方の比較 → テンプレ1・3・9が合いやすい
- 悩み系 → テンプレ2・5・10が合いやすい
- コスパ・プチプラ系 → テンプレ6・8が合いやすい
- NG習慣系 → テンプレ7が合いやすい
- 継続・体験報告系 → テンプレ4が合いやすい
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
- 「〜です」「〜ます」は使わず、フラットな話し言葉で
- AI感のある表現（「〜ですね」「〜でしょう」「まとめると」）は禁止
- 断定禁止、仮説・体験として書く（「〜かもしれない」「気がした」）
- 同じTYPEを3件連続させない
- テンプレートの骨格は維持しつつ、○○部分をトピックに合わせて具体的に埋める
"""


def generate_posts(knowledge: dict, research: str, existing_ids: list) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = GENERATE_PROMPT.format(
        profile=knowledge["profile"],
        target=knowledge["target"],
        genre=knowledge["genre"],
        writing=knowledge["writing"],
        ng_rules=knowledge["ng_rules"],
        templates=TEMPLATES,
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
        template_m = re.search(r'TEMPLATE:\s*(.+)', block)
        topic_m = re.search(r'TOPIC:\s*(.+)', block)
        body_m = re.search(r'BODY:\n(.*?)(?=SELF_REPLY:|===)', block, re.DOTALL)
        reply_m = re.search(r'SELF_REPLY:\n(.*?)$', block, re.DOTALL)

        if not (type_m and topic_m and body_m):
            continue

        topic = topic_m.group(1).strip()
        template = template_m.group(1).strip() if template_m else ""
        # TEMPLATEをtopicに付記（キューで確認できるよう）
        full_topic = f"{topic} [{template}]" if template else topic

        posts.append({
            "type": type_m.group(1).strip(),
            "topic": full_topic,
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
