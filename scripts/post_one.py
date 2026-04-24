"""
Threads 1件投稿スクリプト（GitHub Actions用）
1トリガー = 1投稿。スロットガードで二重投稿を防ぐ。
"""

import os
import re
import sys
import time
import random
import requests

from utils import (
    jst_now,
    LOG_PATH,
    HISTORY_PATH,
    parse_queue,
    update_queue_status,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ACCESS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
USER_ID      = os.environ["THREADS_USER_ID"]
SLOT         = os.environ.get("SLOT", "unknown")  # morning / evening1 / evening2
API          = "https://graph.threads.net/v1.0"


# ─── スロットガード ────────────────────────────────────────────
def already_posted_today(slot: str) -> bool:
    """本日このスロットで投稿済みか確認（二重投稿防止）。
    遅延後に呼ぶことでレースウィンドウを最小化している。
    """
    today = jst_now().strftime("%Y-%m-%d")
    try:
        text = LOG_PATH.read_text(encoding="utf-8")
        return f"slot:{slot} date:{today}" in text
    except FileNotFoundError:
        return False


# ─── Threads API ──────────────────────────────────────────────
def create_container(text: str, reply_to_id: str | None = None) -> str | None:
    data = {"media_type": "TEXT", "text": text, "access_token": ACCESS_TOKEN}
    if reply_to_id:
        data["reply_to_id"] = reply_to_id
    resp = requests.post(
        f"{API}/{USER_ID}/threads", data=data, timeout=30
    )
    if resp.status_code != 200:
        print(f"[ERROR] コンテナ作成失敗: {resp.status_code} {resp.text}")
        return None
    return resp.json().get("id")


def publish_container(container_id: str) -> str | None:
    resp = requests.post(
        f"{API}/{USER_ID}/threads_publish",
        data={"creation_id": container_id, "access_token": ACCESS_TOKEN},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[ERROR] 投稿公開失敗: {resp.status_code} {resp.text}")
        return None
    return resp.json().get("id")


def post_self_reply(reply_text: str, parent_post_id: str) -> str | None:
    print(f"[セルフリプライ] 投稿中（{len(reply_text)}字）...")
    container_id = create_container(reply_text, reply_to_id=parent_post_id)
    if not container_id:
        return None
    print("30秒待機（セルフリプライ）...")
    time.sleep(30)
    reply_id = publish_container(container_id)
    if reply_id:
        print(f"[セルフリプライ完了] reply_id={reply_id}")
    return reply_id


# ─── ファイル更新 ──────────────────────────────────────────────
def append_log(post: dict, post_id: str) -> None:
    now   = jst_now()
    today = now.strftime("%Y-%m-%d")
    entry = (
        f"[POST] {now.strftime('%Y-%m-%d %H:%M JST')} "
        f"| slot:{SLOT} date:{today} "
        f"| ID:{post['id']} | threads_id:{post_id}\n"
    )
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def append_history(post: dict, post_id: str, reply_id: str | None = None) -> None:
    import datetime
    now         = jst_now()
    now_str     = now.strftime("%Y-%m-%d %H:%M JST")
    fetch_after = (now + datetime.timedelta(hours=24)).isoformat()
    reply_line  = f"- reply_id: {reply_id}\n" if reply_id else ""
    entry = f"""
## {post['id']} | {now_str}

- type: {post['type']}
- topic: {post['topic']}
- post_id: {post_id}
{reply_line}- metrics_fetched: false
- posted_at: {now.isoformat()}
- fetch_after: {fetch_after}

### 本文
{post['body']}

---
"""
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


# ─── メイン ───────────────────────────────────────────────────
def main() -> None:
    print(f"=== post_one.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")
    print(f"[スロット] {SLOT}")

    # 1. ランダム遅延（±30分ウィンドウを均等に埋める）
    delay = random.randint(0, 3600)  # 0〜60分
    print(f"[遅延] {delay // 60}分{delay % 60}秒待機...")
    time.sleep(delay)

    # 2. スロットガード（遅延後に判定することでレースウィンドウを最小化）
    if already_posted_today(SLOT):
        print(f"[スキップ] slot:{SLOT} は本日投稿済み。二重投稿を防止して終了。")
        sys.exit(0)

    # 3. キューから1件取得
    posts = parse_queue()
    if not posts:
        print("[INFO] 投稿キューが空です。")
        sys.exit(0)

    post = posts[0]
    print(f"[投稿] ID:{post['id']} TYPE:{post['type']}")
    print(f"本文({len(post['body'])}字): {post['body'][:60]}...")

    # 4. コンテナ作成
    container_id = create_container(post["body"])
    if not container_id:
        sys.exit(1)

    # 5. 30秒待機（Threads API 推奨）
    print("30秒待機（API推奨）...")
    time.sleep(30)

    # 6. 公開
    post_id = publish_container(container_id)
    if not post_id:
        sys.exit(1)

    print(f"[完了] post_id={post_id}")

    # 7. セルフリプライ
    reply_id = None
    if post.get("self_reply"):
        reply_id = post_self_reply(post["self_reply"], post_id)
    else:
        print("[セルフリプライ] なし（スキップ）")

    # 8. ファイル更新
    update_queue_status(post["id"])
    append_log(post, post_id)
    append_history(post, post_id, reply_id)

    print(f"=== 完了 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")


if __name__ == "__main__":
    main()
