"""
Threads 1件投稿スクリプト（GitHub Actions用）
post_queue.md から1件取り出し、確率判定→ランダム遅延→投稿する。
"""

import os
import re
import sys
import time
import random
import datetime
import requests
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
QUEUE_PATH = DATA_DIR / "post_queue.md"
LOG_PATH = DATA_DIR / "post_log.md"
HISTORY_PATH = DATA_DIR / "post-history.md"

ACCESS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
USER_ID = os.environ["THREADS_USER_ID"]
API = "https://graph.threads.net/v1.0"


# ─── 時刻 ────────────────────────────────────────────────

def jst_now() -> datetime.datetime:
    tz = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(tz)


def today_jst() -> str:
    return jst_now().strftime("%Y-%m-%d")


# ─── セッション判定 ───────────────────────────────────────
# 朝セッション: 8:00〜9:30 JST（上限1件）
# 夜セッション: 18:00〜26:00 JST = 18:00〜翌02:00（上限2件）

SESSION_CAPS = {"morning": 1, "evening": 2}


def get_session_info() -> tuple[str, str]:
    """(session_name, session_date) を返す。
    session_date は夜セッション跨ぎ用の基準日（18:00起算）。
    """
    now = jst_now()
    hour = now.hour
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    if 8 <= hour < 10:           # 8:00〜9:59 JST → 朝
        return "morning", today
    elif hour >= 18:              # 18:00〜23:59 JST → 夜（当日）
        return "evening", today
    elif hour < 2:                # 0:00〜1:59 JST → 夜（前日の夜セッション扱い）
        return "evening", yesterday
    else:
        return "other", today


def get_session_count(session: str, session_date: str) -> int:
    """指定セッションの投稿件数をログから取得する。"""
    try:
        text = LOG_PATH.read_text(encoding="utf-8")
        marker = f"session:{session} date:{session_date}"
        return text.count(marker)
    except FileNotFoundError:
        return 0


def should_post(session: str, count: int) -> bool:
    if session == "other":
        print("[スキップ] 投稿時間外（朝8:00〜9:30・夜18:00〜26:00以外）")
        return False
    cap = SESSION_CAPS.get(session, 0)
    if count >= cap:
        print(f"[スキップ] {session}セッション上限に達しています（{count}/{cap}件）")
        return False
    print(f"[投稿] {session}セッション {count + 1}/{cap}件目")
    return True


# ─── キューパース ──────────────────────────────────────────

def parse_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    text = QUEUE_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r'---\n(id:\s*(.+?)\ntype:\s*(.+?)\nstatus:\s*queued\nsource:\s*(.+?)\ntopic:\s*(.+?)\n(?:score_target:\s*(.+?)\n)?created:\s*(.+?)\n)---\n\n(.*?)(?=\n\n---|\Z)',
        re.DOTALL,
    )
    posts = []
    for m in pattern.finditer(text):
        body_block = m.group(8)
        # セルフリプライをHTMLコメントから抽出
        self_reply = ""
        reply_match = re.search(r'<!--\s*self_reply:\n(.*?)\n-->', body_block, re.DOTALL)
        if reply_match:
            self_reply = reply_match.group(1).strip()
        # セルフリプライコメントを除いた本文
        body = re.sub(r'\n*<!--\s*self_reply:.*?-->', '', body_block, flags=re.DOTALL).strip()

        posts.append({
            "id": m.group(2).strip(),
            "type": m.group(3).strip(),
            "source": m.group(4).strip(),
            "topic": m.group(5).strip(),
            "created": m.group(7).strip(),
            "body": body,
            "self_reply": self_reply,
        })
    return posts


# ─── Threads API ──────────────────────────────────────────

def create_container(text: str, reply_to_id: str | None = None) -> str | None:
    data = {"media_type": "TEXT", "text": text, "access_token": ACCESS_TOKEN}
    if reply_to_id:
        data["reply_to_id"] = reply_to_id
    resp = requests.post(
        f"{API}/{USER_ID}/threads",
        data=data,
        timeout=30,
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
    """本文投稿へのセルフリプライを投稿する"""
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


# ─── ファイル更新 ──────────────────────────────────────────

def update_queue_status(post_id: str):
    content = QUEUE_PATH.read_text(encoding="utf-8")
    updated = re.sub(
        r'(id:\s*' + re.escape(post_id) + r'\ntype:\s*.+?\nstatus:\s*)queued',
        r'\g<1>posted',
        content,
        flags=re.DOTALL,
    )
    QUEUE_PATH.write_text(updated, encoding="utf-8")


def append_log(post: dict, post_id: str, session: str, session_date: str, count: int):
    now_str = jst_now().strftime("%Y-%m-%d %H:%M JST")
    entry = f"[POST] {now_str} | session:{session} date:{session_date} | ID:{post['id']} | threads_id:{post_id} | {session}{count}件目\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def append_history(post: dict, post_id: str, reply_id: str | None = None):
    now = jst_now()
    now_str = now.strftime("%Y-%m-%d %H:%M JST")
    fetch_after = (now + datetime.timedelta(hours=24)).isoformat()
    reply_line = f"- reply_id: {reply_id}\n" if reply_id else ""
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


# ─── メイン ───────────────────────────────────────────────

def main():
    print(f"=== post_one.py 開始 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")

    # 1. セッション判定
    session, session_date = get_session_info()
    print(f"[セッション] {session} (基準日: {session_date})")

    # 2. ランダム遅延（人間らしい投稿タイミング）
    delay = random.randint(0, 1200)  # 0〜20分
    print(f"[遅延] {delay // 60}分{delay % 60}秒待機...")
    time.sleep(delay)

    # 3. セッションの投稿件数チェック
    count = get_session_count(session, session_date)
    print(f"[カウント] {session}セッションの投稿数: {count}件")

    if not should_post(session, count):
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

    # 5. 30秒待機
    print("30秒待機（API推奨）...")
    time.sleep(30)

    # 6. 公開
    post_id = publish_container(container_id)
    if not post_id:
        sys.exit(1)

    print(f"[完了] post_id={post_id}")

    # 7. セルフリプライ（テキストがある場合）
    reply_id = None
    if post.get("self_reply"):
        reply_id = post_self_reply(post["self_reply"], post_id)
    else:
        print("[セルフリプライ] なし（スキップ）")

    # 8. ファイル更新
    update_queue_status(post["id"])
    append_log(post, post_id, session, session_date, count + 1)
    append_history(post, post_id, reply_id)

    print(f"=== 完了 | {session}セッション {count + 1}件目 ===")


if __name__ == "__main__":
    main()
