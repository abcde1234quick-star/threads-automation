"""
Threads メトリクス取得スクリプト（GitHub Actions用）
post-history.md を読んで 24h 以上経過した投稿のメトリクスを取得する。
スコア計算式は config.calc_score() に一元化。
リプライ本文は reply_insights.md に蓄積（週次生成のフィードバックに使用）。
"""

import os
import re
import sys
import time
import datetime
import requests

from utils import jst_now, HISTORY_PATH, DATA_DIR, atomic_write
from config import calc_score

REPLY_INSIGHTS_PATH = DATA_DIR / "reply_insights.md"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
API   = "https://graph.threads.net/v1.0"


# ─── API ──────────────────────────────────────────────────────
def get_metrics(post_id: str) -> dict | None:
    resp = requests.get(
        f"{API}/{post_id}/insights",
        params={"metric": "views,likes,replies,reposts,quotes", "access_token": TOKEN},
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"  [ERROR] {post_id}: {resp.status_code} {resp.text[:120]}")
        return None
    # 防御的アクセス: values が空配列でも IndexError を起こさない
    raw: dict = {}
    for item in resp.json().get("data", []):
        vals = item.get("values", [])
        if vals:
            raw[item["name"]] = vals[0].get("value", 0)
    raw["score"] = calc_score(raw)
    return raw


def get_replies(post_id: str) -> list[dict]:
    resp = requests.get(
        f"{API}/{post_id}/replies",
        params={"fields": "text,timestamp", "access_token": TOKEN},
        timeout=20,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("data", [])


# ─── history パーサー ──────────────────────────────────────────
def parse_targets(text: str) -> list[dict]:
    now     = jst_now()
    targets = []
    for block in re.split(r"\n---\n", text):
        post_id_m = re.search(r"- post_id:\s*(\d+)", block)
        if not post_id_m:
            continue
        fetched_m = re.search(r"- metrics_fetched:\s*(\w+)", block)
        if fetched_m and fetched_m.group(1).lower() == "true":
            continue
        fetch_after_m = re.search(r"- fetch_after:\s*(.+)", block)
        if fetch_after_m:
            try:
                fa = datetime.datetime.fromisoformat(fetch_after_m.group(1).strip())
                if now < fa:
                    rem  = fa - now
                    h, m = divmod(int(rem.total_seconds()), 3600)
                    print(f"  スキップ: {post_id_m.group(1)} (解禁まであと{h}時間{m // 60}分)")
                    continue
            except ValueError:
                pass
        topic_m = re.search(r"- topic:\s*(.+)", block)
        targets.append({
            "post_id": post_id_m.group(1),
            "topic":   topic_m.group(1).strip() if topic_m else "不明",
            "block":   block,
        })
    return targets


# ─── リプライインサイト蓄積 ────────────────────────────────────
def append_reply_insights(post: dict, replies: list[dict]) -> None:
    """フォロワーのリプライ本文を reply_insights.md に追記する。
    weekly_job.py が「フォロワーの関心・疑問」として生成プロンプトに使用する。
    """
    if not replies:
        return
    topic = post.get("topic", "不明")[:50]
    lines = []
    for r in replies:
        text = r.get("text", "").strip()
        if not text or len(text) < 5:
            continue
        ts = r.get("timestamp", "")[:10]
        lines.append(f"- [{ts}] [{topic}] {text[:100]}")
    if not lines:
        return
    with open(REPLY_INSIGHTS_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ─── history 更新 ─────────────────────────────────────────────
def update_history(post: dict, metrics: dict, replies: list[dict]) -> None:
    content    = HISTORY_PATH.read_text(encoding="utf-8")
    fetched_at = jst_now().strftime("%Y-%m-%d %H:%M JST")

    lines = [
        "",
        "### メトリクス",
        f"- 取得日時: {fetched_at}",
        f"- views: {metrics.get('views', 0)}",
        f"- likes: {metrics.get('likes', 0)}",
        f"- replies: {metrics.get('replies', 0)}",
        f"- reposts: {metrics.get('reposts', 0)}",
        f"- quotes: {metrics.get('quotes', 0)}",
        f"- **score: {metrics.get('score', 0)}**",
    ]
    if replies:
        lines += ["", "### リプライ一覧"]
        for r in replies:
            lines.append(f"- [{r.get('timestamp','')[:10]}] {r.get('text','')[:80]}")

    metrics_text = "\n".join(lines)
    new_block    = re.sub(r"- metrics_fetched:\s*false", "- metrics_fetched: true", post["block"])
    new_block    = new_block.rstrip() + "\n" + metrics_text + "\n"
    new_content  = content.replace(post["block"], new_block)
    if new_content == content:
        print(f"  [WARN] update_history: {post['post_id']} のブロックが見つからず。更新スキップ。")
        return
    atomic_write(HISTORY_PATH, new_content)


# ─── メイン ───────────────────────────────────────────────────
def main() -> None:
    print(f"=== fetch_metrics.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    if not HISTORY_PATH.exists():
        print("[INFO] post-history.md が存在しません。")
        sys.exit(0)

    targets = parse_targets(HISTORY_PATH.read_text(encoding="utf-8"))
    if not targets:
        print("[INFO] 取得対象なし。")
        sys.exit(0)

    print(f"取得対象: {len(targets)}件")
    ok = ng = 0
    for post in targets:
        print(f"\n取得: {post['post_id']} [{post['topic'][:40]}]")
        metrics = get_metrics(post["post_id"])
        if metrics is None:
            ng += 1
            continue
        print(
            f"  views:{metrics.get('views',0)} likes:{metrics.get('likes',0)} "
            f"replies:{metrics.get('replies',0)} score:{metrics.get('score',0)}"
        )
        replies = get_replies(post["post_id"])
        print(f"  リプライ: {len(replies)}件")
        update_history(post, metrics, replies)
        append_reply_insights(post, replies)   # ← reply_insights.md に蓄積
        print("  → 更新完了")
        ok += 1
        time.sleep(1)  # Threads API レート制限対策

    print(f"\n=== 完了 | 成功:{ok}件 / 失敗:{ng}件 ===")
    if ng > 0:
        sys.exit(1)  # CI に失敗を伝える


if __name__ == "__main__":
    main()
