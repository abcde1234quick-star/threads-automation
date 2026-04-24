"""
週次パフォーマンス分析スクリプト（GitHub Actions用）
fetch_metrics.py の後に自動実行される。

出力: data/performance_summary.md
  - テンプレート別平均スコア
  - タイプ(A/B/C)別平均スコア
  - スロット別平均スコア
  - 高スコアトピック TOP10
  - 機械可読セクション（weekly_job.py が参照）
"""

import re
import sys
from collections import defaultdict

from utils import jst_now, DATA_DIR, HISTORY_PATH, LOG_PATH

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SUMMARY_PATH = DATA_DIR / "performance_summary.md"

# スコアが低いとみなすテンプレートの閾値（2件以上投稿済みが条件）
LOW_SCORE_THRESHOLD = 10.0


# ─── パーサー ────────────────────────────────────────────────────
def parse_scored_posts() -> list[dict]:
    """スコア記録済み投稿を全件パース。post_id（Threads API ID）で重複排除。"""
    if not HISTORY_PATH.exists():
        return []
    text = HISTORY_PATH.read_text(encoding="utf-8")

    seen_post_ids: set[str] = set()
    posts: list[dict] = []

    for block in re.split(r"\n---\n", text):
        # Threads API post_id（数値）とスコアが両方あるブロックのみ対象
        post_id_m = re.search(r"- post_id:\s*(\d{10,})", block)
        score_m   = re.search(r"\*\*score:\s*(\d+)\*\*", block)
        if not (post_id_m and score_m):
            continue

        post_id = post_id_m.group(1)
        if post_id in seen_post_ids:
            continue  # 同一投稿の重複エントリをスキップ
        seen_post_ids.add(post_id)

        # 旧フォーマット "type: TYPE_A" と新フォーマット "- type: TYPE_A" の両方に対応
        type_m    = re.search(r"(?:^|\n)-?\s*type:\s*(TYPE_[ABC])", block)
        topic_m   = re.search(r"(?:^|\n)-?\s*topic:\s*(.+)", block)
        header_m  = re.search(r"## (\S+)\s*\|", block)

        topic    = topic_m.group(1).strip() if topic_m else ""
        # テンプレート番号を抽出: "xxx [テンプレ3（...）]" → "テンプレ3"
        tmpl_m   = re.search(r"\[テンプレ(\d+)", topic)
        template = f"テンプレ{tmpl_m.group(1)}" if tmpl_m else "テンプレなし"

        posts.append({
            "post_id":     post_id,
            "internal_id": header_m.group(1) if header_m else "",
            "score":       int(score_m.group(1)),
            "type":        type_m.group(1).strip() if type_m else "不明",
            "topic":       topic,
            "template":    template,
        })

    return posts


def load_slot_map() -> dict[str, str]:
    """post_log.md から internal_id → slot のマッピングを返す。
    新フォーマット: [POST] date | slot:morning date:... | ID:xxx | threads_id:...
    """
    slot_map: dict[str, str] = {}
    if not LOG_PATH.exists():
        return slot_map
    text = LOG_PATH.read_text(encoding="utf-8")
    for m in re.finditer(r"slot:(\w+)\s+date:\S+\s+\|\s+ID:(\S+)", text):
        slot_map[m.group(2)] = m.group(1)
    return slot_map


# ─── 統計計算 ─────────────────────────────────────────────────────
def _stats(scores: list[int]) -> dict:
    n = len(scores)
    return {
        "count": n,
        "avg":   round(sum(scores) / n, 1),
        "max":   max(scores),
        "min":   min(scores),
    }


def compute_template_stats(posts: list[dict]) -> dict[str, dict]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for p in posts:
        buckets[p["template"]].append(p["score"])
    return {t: _stats(s) for t, s in buckets.items()}


def compute_type_stats(posts: list[dict]) -> dict[str, dict]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for p in posts:
        buckets[p["type"]].append(p["score"])
    return {t: _stats(s) for t, s in buckets.items()}


def compute_slot_stats(posts: list[dict], slot_map: dict[str, str]) -> dict[str, dict]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for p in posts:
        slot = slot_map.get(p["internal_id"], "unknown")
        buckets[slot].append(p["score"])
    return {s: _stats(sc) for s, sc in buckets.items()}


def get_top_topics(posts: list[dict], n: int = 10) -> list[dict]:
    return sorted(posts, key=lambda p: p["score"], reverse=True)[:n]


def get_low_templates(template_stats: dict, threshold: float) -> list[str]:
    return [
        t for t, s in template_stats.items()
        if s["avg"] < threshold and s["count"] >= 2 and t != "テンプレなし"
    ]


# ─── レポート書き出し ─────────────────────────────────────────────
def write_summary(
    posts:          list[dict],
    template_stats: dict,
    type_stats:     dict,
    slot_stats:     dict,
    top_topics:     list[dict],
    low_templates:  list[str],
) -> None:
    now      = jst_now().strftime("%Y-%m-%d %H:%M JST")
    total    = len(posts)
    avg_all  = round(sum(p["score"] for p in posts) / total, 1) if total else 0

    lines: list[str] = [
        "# パフォーマンスサマリー",
        f"更新: {now} | 分析対象: {total}件 | 全体平均スコア: {avg_all}",
        "",
    ]

    # ── テンプレート別 ──────────────────────────────────────────────
    lines += ["## テンプレート別スコア", "",
              "| テンプレ | 件数 | 平均 | 最高 | 最低 | 評価 |",
              "|---------|------|------|------|------|------|"]
    for t, s in sorted(template_stats.items(), key=lambda x: x[1]["avg"], reverse=True):
        if t in low_templates:
            rating = "🔴 要見直し"
        elif s["avg"] >= 30:
            rating = "🟢 優秀"
        else:
            rating = "🟡 普通"
        lines.append(f"| {t} | {s['count']} | {s['avg']} | {s['max']} | {s['min']} | {rating} |")
    lines.append("")

    # ── タイプ別 ────────────────────────────────────────────────────
    lines += ["## タイプ別スコア", "",
              "| タイプ | 件数 | 割合 | 平均 | 最高 |",
              "|--------|------|------|------|------|"]
    for t, s in sorted(type_stats.items(), key=lambda x: x[1]["avg"], reverse=True):
        ratio = round(s["count"] / total * 100) if total else 0
        lines.append(f"| {t} | {s['count']} | {ratio}% | {s['avg']} | {s['max']} |")
    lines.append("")

    # データ駆動のタイプ比率推奨（スコア加重）
    if type_stats:
        total_weighted = sum(s["avg"] * s["count"] for s in type_stats.values())
        lines += ["### データ駆動タイプ比率推奨（スコア重み付き）", ""]
        for t, s in sorted(type_stats.items(), key=lambda x: x[1]["avg"], reverse=True):
            rec = round(s["avg"] * s["count"] / total_weighted * 100) if total_weighted else 0
            cur = round(s["count"] / total * 100) if total else 0
            arrow = "↑" if rec > cur else ("↓" if rec < cur else "→")
            lines.append(f"- {t}: 現在 {cur}% → 推奨 {rec}% {arrow}")
        lines.append("")

    # ── スロット別 ──────────────────────────────────────────────────
    known_slots = {k: v for k, v in slot_stats.items() if k != "unknown"}
    if known_slots:
        lines += ["## スロット別スコア", "",
                  "| スロット | 件数 | 平均 | 最高 |",
                  "|---------|------|------|------|"]
        for slot, s in sorted(known_slots.items(), key=lambda x: x[1]["avg"], reverse=True):
            lines.append(f"| {slot} | {s['count']} | {s['avg']} | {s['max']} |")
        lines.append("")

    # ── 高スコアトピック ─────────────────────────────────────────────
    lines += ["## 高スコアトピック TOP10", ""]
    for i, p in enumerate(top_topics, 1):
        lines.append(f"{i}. score:{p['score']} | {p['type']} | {p['topic'][:70]}")
    lines.append("")

    # ── 要見直しテンプレート ─────────────────────────────────────────
    if low_templates:
        lines += ["## ⚠️ 要見直しテンプレート（平均スコア10未満・2件以上）", ""]
        for t in low_templates:
            s = template_stats[t]
            lines.append(
                f"- {t}: 平均{s['avg']}点 ({s['count']}件) "
                f"→ weekly_job.py で生成頻度を自動抑制"
            )
        lines.append("")

    # ── 機械可読セクション（weekly_job.py が参照する） ──────────────
    best_type = max(type_stats.items(), key=lambda x: x[1]["avg"])[0] if type_stats else "TYPE_A"
    best_slot = (
        max(known_slots.items(), key=lambda x: x[1]["avg"])[0]
        if known_slots else "unknown"
    )
    top3_topics = " / ".join(p["topic"][:50] for p in top_topics[:3])

    lines += [
        "## 機械可読セクション（weekly_job.pyが参照）",
        "<!-- machine-readable-start -->",
        f"LOW_TEMPLATES: {', '.join(low_templates) if low_templates else 'なし'}",
        f"TOP_TOPICS: {top3_topics}",
        f"BEST_TYPE: {best_type}",
        f"BEST_SLOT: {best_slot}",
        f"TOTAL_POSTS: {total}",
        f"AVG_SCORE: {avg_all}",
        "<!-- machine-readable-end -->",
    ]

    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[出力] {SUMMARY_PATH}")


# ─── メイン ──────────────────────────────────────────────────────
def main() -> None:
    print(f"=== analyze_performance.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    posts = parse_scored_posts()
    print(f"スコア付き投稿: {len(posts)}件")

    if not posts:
        print("[INFO] 分析対象なし。performance_summary.md の生成をスキップ。")
        sys.exit(0)

    slot_map       = load_slot_map()
    template_stats = compute_template_stats(posts)
    type_stats     = compute_type_stats(posts)
    slot_stats     = compute_slot_stats(posts, slot_map)
    top_topics     = get_top_topics(posts, n=10)
    low_templates  = get_low_templates(template_stats, LOW_SCORE_THRESHOLD)

    print(f"テンプレート種類: {len(template_stats)}種")
    type_summary = ", ".join(f"{t}:{s['count']}件" for t, s in sorted(type_stats.items()))
    print(f"タイプ分布: {type_summary}")
    if low_templates:
        print(f"⚠️  要見直しテンプレ: {', '.join(low_templates)}")
    best_slot_info = max(slot_stats.items(), key=lambda x: x[1]["avg"]) if slot_stats else None
    if best_slot_info:
        print(f"最高スコアスロット: {best_slot_info[0]} (平均{best_slot_info[1]['avg']})")

    write_summary(posts, template_stats, type_stats, slot_stats, top_topics, low_templates)
    print(f"\n=== 完了 ===")


if __name__ == "__main__":
    main()
