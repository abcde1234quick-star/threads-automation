"""
Threads メトリクス取得スクリプト（GitHub Actions用）
post-history.md を読んで 24h 以上経過した投稿のメトリクスを取得する。
スコア計算式は config.calc_score() に一元化。
"""

import os
import re
import sys
import datetime
import requests

from utils import jst_now, HISTORY_PATH
from config import calc_score

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
    raw = {item["name"]: item["values"][0]["value"] for item in resp.json().get("data", [])}
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
    new_block = re.sub(r"- metrics_fetched:\s*false", "- metrics_fetched: true", post["block"])
    new_block = new_block.rstrip() + "\n" + metrics_text + "\n"
    HISTORY_PATH.write_text(content.replace(post["block"], new_block), encoding="utf-8")


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
        print("  → 更新完了")
        ok += 1

    print(f"\n=== 完了 | 成功:{ok}件 / 失敗:{ng}件 ===")
    if ng > 0:
        sys.exit(1)  # CI に失敗を伝える


if __name__ == "__main__":
    main()
