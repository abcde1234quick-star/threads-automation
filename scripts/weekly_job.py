"""
週次ジョブ（GitHub Actions用）
1. DuckDuckGo で美容トレンドをリサーチ（前週比で急上昇キーワードを検知）
2. analyze_performance.py の出力（performance_summary.md）を読み込む
3. 高スコア投稿 + リプライインサイト をフィードバックとして取得
4. Claude API で 15 件の投稿を生成（弱いテンプレを自動排除）→ post_queue.md に追加

冪等性: 同日2回実行防止（WEEKLY_FORCE=1 で強制上書き）
"""

import os
import re
import sys
from collections import Counter

import anthropic
from duckduckgo_search import DDGS

from utils import jst_now, DATA_DIR, KNOWLEDGE_DIR, HISTORY_PATH, QUEUE_PATH, atomic_write
from config import TEMPLATES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
SUMMARY_PATH        = DATA_DIR / "performance_summary.md"
TREND_HISTORY_PATH  = DATA_DIR / "trend_history.md"
REPLY_INSIGHTS_PATH = DATA_DIR / "reply_insights.md"
GENERATION_LOG_PATH = DATA_DIR / "generation_log.md"


# ─── 冪等性チェック ───────────────────────────────────────────────
def already_generated_today() -> bool:
    """本日既に weekly_job.py が実行済みか確認する。"""
    if not GENERATION_LOG_PATH.exists():
        return False
    today = jst_now().strftime("%Y-%m-%d")
    try:
        return f"[GENERATED] {today}" in GENERATION_LOG_PATH.read_text(encoding="utf-8")
    except Exception:
        return False


def record_generation() -> None:
    """実行ログに本日の生成を記録する。"""
    now = jst_now()
    entry = f"[GENERATED] {now.strftime('%Y-%m-%d')} | {now.strftime('%H:%M JST')}\n"
    with open(GENERATION_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


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

    blocks = re.split(r"\n## \d{4}-\d{2}-\d{2}\n", history)
    if len(blocks) < 2:
        return []
    prev_text = " ".join(blocks[-3:-1]) if len(blocks) >= 3 else blocks[-2]

    def extract_keywords(text: str) -> Counter:
        words = re.findall(r"[ァ-ヶー一-龥]{2,}|[A-Za-z]{3,}", text)
        return Counter(words)

    cur_count  = extract_keywords(current_research)
    prev_count = extract_keywords(prev_text)

    rising = []
    for word, count in cur_count.most_common(50):
        prev = prev_count.get(word, 0)
        if count > prev + 2 and count >= 3:
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
        text    = SUMMARY_PATH.read_text(encoding="utf-8")
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
    lines = [f"## 高スコア実績投稿 TOP{n}（構造・トーン・文体の参考）"]
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
        text    = REPLY_INSIGHTS_PATH.read_text(encoding="utf-8")
        entries = re.findall(r"- \[.+?\] (.+)", text)
        if not entries:
            return "（データ抽出不可）"
        return "\n".join(f"- {e}" for e in entries[-20:])
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

## NG ルール（必ず守ること）
{ng_rules}

## 投稿テンプレート（必ずいずれか1つを選んで使う）
{templates}

## 高スコア実績投稿（構造・トーン・文体の参考）
{top_posts}

## フォロワーのリプライから読み取れる関心・疑問
{reply_insights}

## 今週のリサーチ結果（最新トレンド）
{research}

## 急上昇キーワード（優先的にトピックへ組み込む）
{rising_keywords}

## 次回テーマ候補
{next_topics}

## 既存キューID（重複禁止）
{existing_ids}

## パフォーマンスデータ
{perf_guidance}

---

## ■ 生成指示（Threads 2026 アルゴリズム最適化版）

### 設計思想（最初に読むこと）

この投稿の最優先目的は「情報を伝える」ではなく「反応させる」。
Threads のアルゴリズムが最も評価するシグナルは返信数とスレッド深度。
情報量を増やすほど読了率が下がり、スコアが下がる。
「短くて引きが強い」が「長くて正確」に必ず勝つ。

---

### TYPE 比率と文字数（厳守）

TYPE 比率: {type_ratio}

【本文（BODY）文字数】  絶対上限を超えた場合はその投稿を再生成すること。

- TYPE_C（共感）: 最適 60〜110字 ／ 絶対上限 130字
- TYPE_B（検証）: 最適 90〜140字 ／ 絶対上限 160字
- TYPE_A（比較）: 最適 110〜155字 ／ 絶対上限 180字

【セルフリプライ（SELF_REPLY）】
- 最適 40〜70字 ／ 絶対上限 85字（全 TYPE 共通）

文字数カウントはスペース・改行を除く。

---

### テンプレート選択

トピックの性質に最も合うテンプレートを1つ選ぶ:
- 成分・使い方の比較 → テンプレ1・3・9
- 悩み系・共感系   → テンプレ2・5・10
- コスパ・プチプラ系 → テンプレ6・8
- NG習慣・警告系   → テンプレ7
- 継続・体験報告系  → テンプレ4
- 同じテンプレートを連続3件以上使わない
{template_restriction}

【テンプレート使用上の注意】
テンプレートは「構造の骨格」として使い、以下の要素は必ず書き換えること:
- 「保存して」「コメントで教えて」「ぜひ試してみて」等の CTA フレーズ
- ◎△✗ の表形式（比較は文章で表現すること）
- 「実は」「知らない人多い」（全体で2件以内に制限）

---

### 本文 6 原則（全件必須）

**原則1. 1行目は「読み続けさせる問い」を作る**

1行目の役割はスクロールを止めること、そして2行目を読ませること。
1行目で結論・答え・価値を言い切ることは禁止。
以下4種のうち1つで始めること:

▶ 損失回避型（状況提示→危機感）
  先頭例: 「それ、逆効果かも」「続けてると悪化する」「気づかないうちに○○」
  NG: 「○○はNG！」（断定で終わり、続きを読む必要がない）

▶ 自己開示型（行動・判断の告白）
  先頭例: 「正直に言う。○○やめた」「去年まで全然わかってなかった」「3年間ずっと間違えてた」
  NG: 「私の体験談です」（告白感がなくプロローグで終わっている）

▶ 驚き・反転型（常識との矛盾提示）
  先頭例: 「○○と○○、順番が逆だった」「高いほうが効くと思ってたけど」「むしろ何もしないほうが」
  NG: 「○○について知っていますか？」（質問形式の驚き型は弱い）

▶ 共感問いかけ型（読者の状況を名指しする）
  先頭例: 「○○してるのに何か違う、の正体が分かった」「また同じ悩みが戻ってきた」
  NG: 「○○に悩んでいる方へ」（宣言型は読者が自分事化しにくい）

**原則2. 中盤に「転換」を1回入れる**

1行目が問いなら、中盤でその答えではなく「意外な角度」を1つ入れる。
転換は「でも」「ただ」「逆に」「ここが問題で」で始まる1文で表現する。
転換がない投稿はまっすぐで止まる力がない。

**原則3. 末尾は「コメント余白」で終える**

末尾の役割は「読者が自分の経験を重ねられる隙間を作る」こと。
OKパターン（余白あり）:
  「これ、私だけじゃないと思うんだけどな」
  「……また同じもの買ってた」
  「結局どっちが正解かは、まだわかってない」
NGパターン（余白なし）:
  「みんなはどう思う？コメントして！」 ← 露骨CTA
  「参考になれば嬉しいです」          ← 完結して余白がない
  「試してみてね！」                  ← 指示で余白がない

**原則4. 1投稿1論点**

1つの投稿で伝えることは1つだけ。
2つ以上の主張・比較軸・結論が入ってはいけない。

**原則5. 改行設計（読ませるリズム）**

1〜2文で改行する。3行以上の連続テキスト禁止。
①②③ の箇条書きは最大2行まで。横線（────）・表形式禁止。

**原則6. 話し言葉の徹底**

「〜です」「〜ます」は使わない。
友人に話すような一人称トーン。体験の途中にいる人として書く。

---

### セルフリプライ「2話目」設計

セルフリプライは本文の続き・補足・まとめではない。「2話目の1行目」として書く。

セルフリプライを読んだ人が「本文に戻って読み直したくなる」か
「これってどういうこと？とコメントしたくなる」状態を作る。

使えるパターン:
- 追撃型:「でも正直、ここが一番引っかかってて─」（本文の問いをさらに深掘り）
- 反転型:「逆に考えると○○なのかもって思ってて」（本文の前提をひっくり返す）
- 宙吊り型:「結論は出てないけど、今は○○だけやめてる」（結論の1歩手前で止める）
- 読者鏡型:「もしかして同じことしてた人、いるかな」（「私もそう」コメントを誘発）

禁止:
- 「まとめると○○です」      ← 結論（補足型）
- 「詳しくは○○でも書きます」 ← 宣伝
- 「参考になれば嬉しいです」  ← 完結
- 「なので○○を試してください」← CTA

---

### 言葉遣い・絶対禁止

表現:
- 「〜です」「〜ます」「〜ですね」「〜でしょう」
- 「まとめると」「ポイントは」「〜について解説します」「今回は〜を紹介」
- 「ちなみに」「そして」「また」「さらに」（接続詞過多→note記事感）
- 「実は」「知らない人が多い」→ 全体で2件まで。それ以上は使わない

行動指示:
- 「保存して」「コメントして」「フォローして」「いいねして」
- 「役に立ったら」「参考になれば」「シェアしてほしい」
- 「試してみてね」「ぜひやってみて」

構造:
- 断定表現（「〜です」「〜が正解」「絶対〜」）
- 3つ以上の連続箇条書き（①②③ が3行以上）
- 表形式（◎△✗ の縦並び比較）
- 記事風タイトル→本文（「○○の方法3選」「○○まとめ」）

---

### 出力フォーマット

===POST_START===
TYPE: TYPE_A
TEMPLATE: テンプレ○（テンプレート名）
TOPIC: （テーマ）
BODY:
（本文。文字数上限を厳守。1行目は「読み続けさせる問い」。中盤に転換1回。末尾はコメント余白。）
SELF_REPLY:
（2話目の1行目として書く。40〜70字。補足・まとめ・結論禁止。）
===SELFCHECK===
[1] 1行目のフック種別: 損失回避型 / 自己開示型 / 驚き反転型 / 共感問いかけ型
[2] 1行目を読んだだけで2行目を読みたくなるか: YES / NO → NO なら再生成
[3] 本文に「転換」が1回入っているか: YES / NO → NO なら再生成
[4] 末尾にコメント余白があるか: YES / NO → NO なら再生成
[5] 文字数(本文): ○字（上限○字） → 上限超過なら再生成
[6] 文字数(セルフリプ): ○字（上限85字） → 上限超過なら再生成
[7] セルフリプ設計: 追撃型 / 反転型 / 宙吊り型 / 読者鏡型 → 「補足型」なら再生成
[8] 禁止表現チェック: なし / あり（該当箇所: ） → ありなら再生成
[9] 1投稿1論点になっているか: YES / NO → NO なら再生成
===SELFCHECK===
※ 再生成条件に該当する場合は修正した投稿を出力すること。
===POST_END===
"""


def build_perf_guidance(perf: dict, rising_keywords: list[str]) -> str:
    """パフォーマンスデータから生成への指示文を作る。"""
    lines = []

    if perf["avg_score"] > 0:
        lines.append(f"現在の全体平均スコア: {perf['avg_score']}点 / 分析投稿数: {perf['total_posts']}件")

    if perf["low_templates"]:
        lines.append(f"⚠️  以下テンプレートは最近スコアが低い。今週は使用頻度を下げること: {', '.join(perf['low_templates'])}")

    if perf["top_topics"]:
        lines.append(f"✅ 高スコア実績トピック（関連テーマを優先すること）: {perf['top_topics']}")

    if rising_keywords:
        lines.append(f"🔥 急上昇キーワード（必ず2件以上でトピックに組み込む）: {', '.join(rising_keywords)}")

    # performance_summary.md からタイプ別スコアとスロット情報を抽出して明示する
    if SUMMARY_PATH.exists():
        try:
            text = SUMMARY_PATH.read_text(encoding="utf-8")

            type_scores: dict[str, float] = {}
            for m in re.finditer(r"\|\s*(TYPE_[ABC])\s*\|\s*\d+\s*\|\s*\d+%\s*\|\s*([\d.]+)\s*\|", text):
                type_scores[m.group(1)] = float(m.group(2))

            if type_scores:
                best_t  = max(type_scores, key=lambda k: type_scores[k])
                worst_t = min(type_scores, key=lambda k: type_scores[k])
                lines.append(
                    "\n📊 タイプ別スコア実績: "
                    + " / ".join(f"{t}={v}点" for t, v in sorted(type_scores.items()))
                )
                lines.append(
                    f"   → {best_t}（平均{type_scores[best_t]}点）が最高。"
                    f"今回は{best_t}の構造・トーン・フック種別を最優先で模倣すること。"
                    f"（{worst_t} 平均{type_scores[worst_t]}点 は比率を下げる）"
                )

            slot_scores: dict[str, float] = {}
            for m in re.finditer(r"\|\s*(morning|evening1|evening2)\s*\|\s*\d+\s*\|\s*([\d.]+)\s*\|", text):
                slot_scores[m.group(1)] = float(m.group(2))
            if slot_scores:
                best_slot = max(slot_scores, key=lambda k: slot_scores[k])
                lines.append(f"   → 最高スコアスロット: {best_slot}（平均{slot_scores[best_slot]}点）")

        except Exception as e:
            print(f"  [WARN] guidance 拡張解析エラー: {e}")

    return "\n".join(lines) if lines else "（パフォーマンスデータなし）"


def build_type_ratio(perf: dict) -> str:
    """パフォーマンスデータから動的タイプ比率を返す。

    performance_summary.md の「データ駆動タイプ比率推奨」を直接読んで反映する。
    データが不十分な場合はデフォルト値（A50/B30/C20）を返す。
    """
    def ratio_to_counts(ratios: dict) -> str:
        """{'TYPE_C': 40, 'TYPE_B': 35, 'TYPE_A': 25} -> 生成指示文字列（15件換算）"""
        total = 15
        sorted_types = sorted(ratios.items(), key=lambda x: x[1], reverse=True)
        counts = []
        assigned = 0
        for i, (t, pct) in enumerate(sorted_types):
            label = t.replace("TYPE_", "")
            name  = {"A": "比較", "B": "検証", "C": "共感"}.get(label, label)
            n = total - assigned if i == len(sorted_types) - 1 else round(total * pct / 100)
            assigned += n
            counts.append(f"{label}（{name}）{pct}% → {n}件")
        return "、".join(counts)

    # データが少ない場合はデフォルト
    if perf["total_posts"] < 10:
        return ratio_to_counts({"TYPE_A": 50, "TYPE_B": 30, "TYPE_C": 20})

    # performance_summary.md の推奨比率セクションを直接読む
    # 「- TYPE_C: 現在 22% → 推奨 37% ↑」のような行を抽出
    recommended: dict[str, int] = {}
    if SUMMARY_PATH.exists():
        try:
            text = SUMMARY_PATH.read_text(encoding="utf-8")
            for m in re.finditer(
                r"-\s*(TYPE_[ABC]):\s*現在\s*\d+%\s*→\s*推奨\s*(\d+)%", text
            ):
                recommended[m.group(1)] = int(m.group(2))
        except Exception:
            pass

    if len(recommended) == 3:
        total_pct = sum(recommended.values())
        if total_pct > 0:
            normalized = {k: round(v * 100 / total_pct) for k, v in recommended.items()}
            return ratio_to_counts(normalized)

    # フォールバック: best_type ベースの固定パターン
    best = perf["best_type"]
    if best == "TYPE_C":
        return ratio_to_counts({"TYPE_C": 40, "TYPE_B": 35, "TYPE_A": 25})
    elif best == "TYPE_B":
        return ratio_to_counts({"TYPE_B": 40, "TYPE_A": 35, "TYPE_C": 25})
    else:
        return ratio_to_counts({"TYPE_A": 40, "TYPE_B": 35, "TYPE_C": 25})


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
        type_m      = re.search(r"TYPE:\s*(\S+)", block)
        template_m  = re.search(r"TEMPLATE:\s*(.+)", block)
        topic_m     = re.search(r"TOPIC:\s*(.+)", block)
        body_m      = re.search(r"BODY:\n(.*?)(?=SELF_REPLY:|===|\Z)", block, re.DOTALL)
        # SELFCHECK ブロックより前で止める（SELFCHECK 内容が SELF_REPLY に混入するバグ対策）
        reply_m     = re.search(r"SELF_REPLY:\n(.*?)(?=\n===SELFCHECK===|\Z)", block, re.DOTALL)
        selfcheck_m = re.search(r"===SELFCHECK===\n(.*?)===SELFCHECK===", block, re.DOTALL)

        if not (type_m and topic_m and body_m):
            print(f"  [WARN] パース失敗ブロック: {block[:60]!r}")
            continue

        # SELFCHECK QA ログ（NO / 補足型 / 末尾余白なし を警告表示）
        if selfcheck_m:
            sc_text  = selfcheck_m.group(1)
            ng_items = [l for l in sc_text.splitlines()
                        if "NO" in l or "補足型" in l or ("末尾余白" in l and "なし" in l)]
            if ng_items:
                topic_label = topic_m.group(1).strip()[:30]
                print(f"  [QA] {topic_label} - チェック警告 {len(ng_items)}件: {'; '.join(ng_items)}")

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
    """生成済み投稿を post_queue.md に atomic_write で追記する。"""
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

    # atomic_write: 既存内容を読んで末尾に追記してから原子的に書き込む
    existing = QUEUE_PATH.read_text(encoding="utf-8") if QUEUE_PATH.exists() else ""
    new_content = existing.rstrip("\n") + "\n\n" + "\n".join(entries)
    atomic_write(QUEUE_PATH, new_content)

    print(f"[追加] {len(posts)}件を post_queue.md に追加")
    return len(posts)


# ─── メイン ──────────────────────────────────────────────────────
def main() -> None:
    print(f"=== weekly_job.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    # 冪等性チェック: queue補充チェック時に同日2回実行を防ぐ
    # WEEKLY_FORCE=1 を設定すれば強制実行
    is_monday    = os.environ.get("GITHUB_SCHEDULE", "") == "0 22 * * 0"
    is_dispatch  = os.environ.get("GITHUB_EVENT_NAME", "") == "workflow_dispatch"
    force        = os.environ.get("WEEKLY_FORCE", "") == "1"

    if not (is_monday or is_dispatch or force):
        if already_generated_today():
            print(f"[SKIP] 本日 ({jst_now().strftime('%Y-%m-%d')}) は既に生成済み。終了。")
            print("       強制実行する場合は WEEKLY_FORCE=1 を設定してください。")
            sys.exit(0)

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
        print(f"  最高評価TYPE: {perf['best_type']} / TYPE比率モード: {'動的' if perf['total_posts'] >= 30 else 'デフォルト'}")
        if perf["low_templates"]:
            print(f"  ⚠️  低スコアテンプレ: {', '.join(perf['low_templates'])}")
    else:
        print("  データなし（初回生成）")

    # 4. フィードバック収集（高スコア投稿 + リプライ）
    print("\n[4/5] フィードバックデータを収集中...")
    top_posts      = load_top_posts(n=5)
    reply_insights = load_reply_insights()
    print("  リプライインサイト: 読み込み完了")

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
    queued = len([l for l in QUEUE_PATH.read_text(encoding="utf-8").splitlines()
                  if l.strip() == "status: queued"])
    print(f"\n=== 完了 | 追加{count}件 | キュー残り{queued}件 ===")

    # 生成ログ記録（冪等性チェック用）
    record_generation()


if __name__ == "__main__":
    main()
