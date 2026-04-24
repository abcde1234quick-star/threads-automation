"""
週次ジョブ（GitHub Actions用）
1. DuckDuckGo で美容トレンドをリサーチ（前週比で急上昇キーワードを検知）
2. analyze_performance.py の出力（performance_summary.md）を読み込む
3. 高スコア投稿 + リプライインサイト をフィードバックとして取得
4. Claude API で 15 件の投稿を生成（弱いテンプレを自動排除）→ post_queue.md に追加
"""

import os
import re
import sys
from collections import Counter

import anthropic
from duckduckgo_search import DDGS

from utils import jst_now, DATA_DIR, KNOWLEDGE_DIR, HISTORY_PATH, QUEUE_PATH
from config import TEMPLATES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
SUMMARY_PATH       = DATA_DIR / "performance_summary.md"
TREND_HISTORY_PATH = DATA_DIR / "trend_history.md"
REPLY_INSIGHTS_PATH = DATA_DIR / "reply_insights.md"


# ─── リサーチ ──────────────────────────────────────────────────────
def search_beauty_trends() -> str:
    queries = [
        "2026 スキンケア トレンド 日本 話題",
        "美容 成分 比較 効果 口コミ",
        "site:youtube.com 美容 スキンケア レビュー",
        "美容 新発売 コスメ 話題",
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
    return "\n".join(results[:24])


def save_trend_history(research: str) -> None:
    """今週の検索結果を trend_history.md に追記する。"""
    today = jst_now().strftime("%Y-%m-%d")
    entry = f"\n## {today}\n\n{research}\n"
    with open(TREND_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def detect_rising_keywords(current_research: str) -> list[str]:
    """前週比で急上昇しているキーワードを返す（上位5件）。"""
    if not TREND_HISTORY_PATH.exists():
        return []
    try:
        history = TREND_HISTORY_PATH.read_text(encoding="utf-8")
    except Exception:
        return []

    # 直近2週分のブロックを取得（最新は除く）
    blocks = re.split(r"\n## \d{4}-\d{2}-\d{2}\n", history)
    if len(blocks) < 2:
        return []
    prev_text = " ".join(blocks[-3:-1]) if len(blocks) >= 3 else blocks[-2]

    # 簡易キーワード抽出（2文字以上の日本語 or 英単語）
    def extract_keywords(text: str) -> Counter:
        words = re.findall(r"[ァ-ヶー一-龥]{2,}|[A-Za-z]{3,}", text)
        return Counter(words)

    cur_count  = extract_keywords(current_research)
    prev_count = extract_keywords(prev_text)

    rising = []
    for word, count in cur_count.most_common(50):
        prev = prev_count.get(word, 0)
        if count > prev + 2 and count >= 3:  # 今週3回以上、かつ先週より2回以上増加
            rising.append(word)
    return rising[:5]


# ─── パフォーマンスサマリー読み込み ──────────────────────────────
def load_performance_insights() -> dict:
    """analyze_performance.py が生成した performance_summary.md を読み込む。"""
    defaults = {
        "low_templates":  [],
        "top_topics":     "",
        "best_type":      "TYPE_A",
        "best_slot":      "unknown",
        "total_posts":    0,
        "avg_score":      0.0,
    }
    if not SUMMARY_PATH.exists():
        return defaults

    try:
        text  = SUMMARY_PATH.read_text(encoding="utf-8")
        block_m = re.search(r"<!-- machine-readable-start -->(.*?)<!-- machine-readable-end -->",
                            text, re.DOTALL)
        if not block_m:
            return defaults
        block = block_m.group(1)

        def _get(key: str) -> str:
            m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
            return m.group(1).strip() if m else ""

        low_raw = _get("LOW_TEMPLATES")
        return {
            "low_templates": [t.strip() for t in low_raw.split(",") if t.strip() and t.strip() != "なし"],
            "top_topics":    _get("TOP_TOPICS"),
            "best_type":     _get("BEST_TYPE") or "TYPE_A",
            "best_slot":     _get("BEST_SLOT") or "unknown",
            "total_posts":   int(_get("TOTAL_POSTS") or 0),
            "avg_score":     float(_get("AVG_SCORE") or 0),
        }
    except Exception as e:
        print(f"  [WARN] performance_summary.md 読み込みエラー: {e}")
        return defaults


# ─── 知識ファイル ──────────────────────────────────────────────────
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


# ─── フィードバックループ：高スコア投稿 ──────────────────────────
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
    lines = [f"## 高スコア実績投稿 TOP{n}（構造・トーンを参考にすること）"]
    for i, p in enumerate(scored[:n], 1):
        lines.append(f"\n### {i}位 score:{p['score']} | {p['topic'][:35]}")
        lines.append(p["body"])
    return "\n".join(lines)


# ─── フィードバックループ：リプライインサイト ────────────────────
def load_reply_insights() -> str:
    """reply_insights.md から最近のフォロワーの疑問・関心を返す。"""
    if not REPLY_INSIGHTS_PATH.exists():
        return "（まだデータなし）"
    try:
        text = REPLY_INSIGHTS_PATH.read_text(encoding="utf-8")
        # 直近20件のリプライテキストを返す
        entries = re.findall(r"- \[.+?\] (.+)", text)
        if not entries:
            return "（データ抽出不可）"
        recent = entries[-20:]
        return "\n".join(f"- {e}" for e in recent)
    except Exception:
        return "（読み込みエラー）"


# ─── 投稿生成 ──────────────────────────────────────────────────────
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

## フォロワーのリプライから読み取れる関心・疑問（次の投稿ネタに活用）
{reply_insights}

## 今週のリサーチ結果（最新トレンド）
{research}

## 今週の急上昇キーワード（これらを優先的にトピックへ組み込む）
{rising_keywords}

## 現在の次回テーマ候補
{next_topics}

## 既存キューID（重複禁止）
{existing_ids}

## パフォーマンスデータ（今週の戦略調整）
{perf_guidance}

---

## 生成指示

以下のフォーマットで15件生成してください。
TYPE比率: {type_ratio}

テンプレート選択ルール:
- トピックの性質に最も合うテンプレートを1つ選ぶ
- 成分・使い方の比較 → テンプレ1・3・9
- 悩み系 → テンプレ2・5・10
- コスパ・プチプラ系 → テンプレ6・8
- NG習慣系 → テンプレ7
- 継続・体験報告系 → テンプレ4
- 同じテンプレートを連続3件以上使わない
{template_restriction}

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


def build_perf_guidance(perf: dict, rising_keywords: list[str]) -> str:
    """パフォーマンスデータから生成への指示文を作る。"""
    lines = []
    if perf["low_templates"]:
        lines.append(f"⚠️  以下テンプレートは最近スコアが低い。今週は使用頻度を下げること: {', '.join(perf['low_templates'])}")
    if perf["top_topics"]:
        lines.append(f"✅ 高スコア実績トピック（関連テーマを優先すること）: {perf['top_topics']}")
    if perf["avg_score"] > 0:
        lines.append(f"現在の全体平均スコア: {perf['avg_score']}点 / 分析投稿数: {perf['total_posts']}件")
    if rising_keywords:
        lines.append(f"🔥 急上昇キーワード（必ず2件以上でトピックに組み込む）: {', '.join(rising_keywords)}")
    return "\n".join(lines) if lines else "（パフォーマンスデータなし）"


def build_type_ratio(perf: dict) -> str:
    """パフォーマンスデータから動的タイプ比率を返す。データ不足時はデフォルト。"""
    # 現状はデフォルト比率を返す。データが十分になったら動的化。
    # TODO: total_posts >= 30 になったら type_stats から動的計算
    return "A（比較）50% → 8件、B（検証）30% → 4件、C（共感）20% → 3件"


def build_template_restriction(perf: dict) -> str:
    if not perf["low_templates"]:
        return ""
    return f"- 以下テンプレートは今週使用禁止: {', '.join(perf['low_templates'])}"


def generate_posts(
    knowledge: dict,
    research: str,
    top_posts: str,
    reply_insights: str,
    rising_keywords: list[str],
    existing_ids: list[str],
    perf: dict,
) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = GENERATE_PROMPT.format(
        profile=knowledge["profile"],
        target=knowledge["target"],
        genre=knowledge["genre"],
        writing=knowledge["writing"],
        ng_rules=knowledge["ng_rules"],
        templates=TEMPLATES,
        top_posts=top_posts,
        reply_insights=reply_insights,
        research=research,
        rising_keywords=", ".join(rising_keywords) if rising_keywords else "（検知なし）",
        next_topics=load_next_topics(),
        existing_ids=", ".join(existing_ids[-20:]) if existing_ids else "なし",
        perf_guidance=build_perf_guidance(perf, rising_keywords),
        type_ratio=build_type_ratio(perf),
        template_restriction=build_template_restriction(perf),
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ─── パース & キュー追記 ──────────────────────────────────────────
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
    today        = jst_now().strftime("%Y-%m-%d")
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


# ─── メイン ──────────────────────────────────────────────────────
def main() -> None:
    print(f"=== weekly_job.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    # 1. リサーチ + トレンド履歴保存
    print("\n[1/5] 美容トレンドをリサーチ中...")
    research = search_beauty_trends()
    print(f"  取得: {len(research)}文字")
    save_trend_history(research)

    # 2. 急上昇キーワード検知
    print("\n[2/5] 急上昇キーワードを検知中...")
    rising_keywords = detect_rising_keywords(research)
    if rising_keywords:
        print(f"  🔥 急上昇: {', '.join(rising_keywords)}")
    else:
        print("  検知なし（初回 or 前週データなし）")

    # 3. パフォーマンスインサイト読み込み
    print("\n[3/5] パフォーマンスインサイトを読み込み中...")
    perf = load_performance_insights()
    if perf["total_posts"] > 0:
        print(f"  平均スコア: {perf['avg_score']} / 分析投稿数: {perf['total_posts']}件")
        if perf["low_templates"]:
            print(f"  ⚠️  低スコアテンプレ: {', '.join(perf['low_templates'])}")
    else:
        print("  データなし（初回生成）")

    # 4. フィードバック収集（高スコア投稿 + リプライ）
    print("\n[4/5] フィードバックデータを収集中...")
    top_posts      = load_top_posts(n=5)
    reply_insights = load_reply_insights()
    print(f"  リプライインサイト: 読み込み完了")

    # 5. 投稿生成
    print("\n[5/5] Claude API で投稿生成中（15件）...")
    knowledge    = load_knowledge()
    existing_ids = load_queue_ids()
    generated    = generate_posts(
        knowledge, research, top_posts,
        reply_insights, rising_keywords, existing_ids, perf,
    )

    # キューへ追加
    print("\n[追加] post_queue.md に追記中...")
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
