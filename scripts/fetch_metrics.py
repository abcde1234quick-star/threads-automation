"""
Threads メトリクス取得スクリプト（GitHub Actions用）
post-history.md を読んで24時間以上経過した投稿のメトリクスを取得する。
"""

import os
import re
import sys
import datetime
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
HISTORY_PATH = BASE_DIR / "data" / "post-history.md"

TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
API = "https://graph.threads.net/v1.0"


def jst_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))


def get_metrics(post_id: str) -> dict | None:
    resp = requests.get(
        f"{API}/{post_id}/insights",
        params={"metric": "views,likes,replies,reposts,quotes", "access_token": TOKEN},
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"  [ERROR] {post_id}: {resp.text[:100]}")
        return None
    data = {item["name"]: item["values"][0]["value"] for item in resp.json().get("data", [])}
    data["score"] = (
        data.get("views", 0)
        + data.get("likes", 0) * 2
        + data.get("replies", 0) * 3
        + data.get("reposts", 0) * 2
        + data.get("quotes", 0) * 2
    )
    return data


def get_replies(post_id: str) -> list[dict]:
    resp = requests.get(
        f"{API}/{post_id}/replies",
        params={"fields": "text,timestamp", "access_token": TOKEN},
        timeout=20,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("data", [])


def parse_targets(text: str) -> list[dict]:
    now = jst_now()
    targets = []
    for block in re.split(r'\n---\n', text):
        post_id_m = re.search(r'- post_id:\s*(\d+)', block)
        if not post_id_m:
            continue
        fetched_m = re.search(r'- metrics_fetched:\s*(\w+)', block)
        if fetched_m and fetched_m.group(1).lower() == "true":
            continue
        fetch_after_m = re.search(r'- fetch_after:\s*(.+)', block)
        if fetch_after_m:
            try:
                fetch_after = datetime.datetime.fromisoformat(fetch_after_m.group(1).strip())
                if now < fetch_after:
                    rem = fetch_after - now
                    h, m = int(rem.total_seconds() // 3600), int((rem.total_seconds() % 3600) // 60)
                    print(f"  スキップ: {post_id_m.group(1)} (解禁まであと{h}時間{m}分)")
                    continue
            except ValueError:
                pass
        topic_m = re.search(r'- topic:\s*(.+)', block)
        targets.append({
            "post_id": post_id_m.group(1),
            "topic": topic_m.group(1).strip() if topic_m else "不明",
            "block": block,
        })
    return targets


def update_history(post: dict, metrics: dict, replies: list[dict]):
    content = HISTORY_PATH.read_text(encoding="utf-8")
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
    new_block = re.sub(
        r'- metrics_fetched:\s*false',
        '- metrics_fetched: true',
        post["block"],
    )
    new_block = new_block.rstrip() + "\n" + metrics_text + "\n"
    HISTORY_PATH.write_text(content.replace(post["block"], new_block), encoding="utf-8")


def main():
    print(f"=== fetch_metrics.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    if not HISTORY_PATH.exists():
        print("[INFO] post-history.md が存在しません。")
        sys.exit(0)

    targets = parse_targets(HISTORY_PATH.read_text(encoding="utf-8"))
    if not targets:
        print("[INFO] 取得対象なし。")
        sys.exit(0)

    print(f"取得対象: {len(targets)}件")
    for post in targets:
        print(f"\n取得: {post['post_id']} [{post['topic']}]")
        metrics = get_metrics(post["post_id"])
        if metrics is None:
            continue
        print(f"  views:{metrics.get('views',0)} likes:{metrics.get('likes',0)} score:{metrics.get('score',0)}")
        replies = get_replies(post["post_id"])
        print(f"  リプライ: {len(replies)}件")
        update_history(post, metrics, replies)
        print("  → 更新完了")

    print("\n=== 完了 ===")


if __name__ == "__main__":
    main()
