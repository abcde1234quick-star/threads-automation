"""
Threads 1件投稿スクリプト（GitHub Actions用）
1トリガー = 1投稿。スロットガード + CLAIM機構で二重投稿を防ぐ。

SRE hardening:
- SLOT 未設定なら起動時エラー（"unknown" フォールバックを廃止）
- claim_slot() で API 呼び出し前にスロットを確保（TOCTOU 対策）
- already_posted_today() が [POST] と [CLAIM] の両方を確認
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

# SLOT バリデーション: 不正値なら起動時に即終了（ACCESS_TOKEN より先に評価）
SLOT = os.environ.get("SLOT", "")
_VALID_SLOTS = ("morning", "evening1", "evening2")
if SLOT not in _VALID_SLOTS:
    print(f"[ERROR] 環境変数 SLOT が未設定または不正: {SLOT!r}. 期待値: {_VALID_SLOTS}")
    sys.exit(1)

ACCESS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
USER_ID      = os.environ["THREADS_USER_ID"]
API          = "https://graph.threads.net/v1.0"


# ─── スロットガード ────────────────────────────────────────────
def already_posted_today(slot: str) -> bool:
    """本日このスロットで投稿済み（[POST]）または確保済み（[CLAIM]）か確認。
    [CLAIM] も確認することで claim_slot() との競合を検知する。
    """
    today = jst_now().strftime("%Y-%m-%d")
    marker = f"slot:{slot} date:{today}"
    try:
        text  = LOG_PATH.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if marker in l]
        return any(l.startswith("[POST]") or l.startswith("[CLAIM]") for l in lines)
    except FileNotFoundError:
        return False


def claim_slot(slot: str) -> bool:
    """API 呼び出し前にスロットを確保する（TOCTOU 対策）。

    動作:
    1. [CLAIM] マーカーをログに書き込む
    2. 0.5 秒待って競合ジョブの書き込みを待つ
    3. ログを再読して自分の CLAIM が先着かどうか確認

    Returns:
        True  = 自分が先着 → 投稿を進めてよい
        False = 別ジョブが先着または既に投稿済み → スキップ
    """
    today = jst_now().strftime("%Y-%m-%d")
    nonce = f"{time.monotonic():.6f}"  # このプロセス固有の識別子
    claim_line = (
        f"[CLAIM] {jst_now().strftime('%Y-%m-%d %H:%M:%S JST')} "
        f"| slot:{slot} date:{today} | nonce:{nonce}\n"
    )
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(claim_line)

    time.sleep(0.5)  # 競合ジョブの書き込みを待つ

    try:
        text = LOG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False

    marker = f"slot:{slot} date:{today}"
    lines  = [l for l in text.splitlines() if marker in l]

    # 既に [POST] がある → 別ジョブが投稿完了済み
    if any(l.startswith("[POST]") for l in lines):
        print(f"[CLAIM] 別ジョブが既に [POST] を記録済み。スキップ。")
        return False

    # [CLAIM] が複数ある → 先着順で判定
    claims = [l for l in lines if l.startswith("[CLAIM]")]
    if not claims:
        print(f"[CLAIM] CLAIM エントリが見つからない（予期しない状態）。スキップ。")
        return False

    if nonce not in claims[0]:
        print(f"[CLAIM] 別ジョブが先着。自分のノンス:{nonce[:8]}... スキップ。")
        return False

    return True


# ─── Threads API ──────────────────────────────────────────────
def _check_token_error(resp: requests.Response, context: str) -> None:
    """401 / 190 エラーをトークン失効として検知し、即 sys.exit(2) する。"""
    if resp.status_code == 401:
        print(
            f"[ERROR] {context}: 401 Unauthorized — アクセストークンが失効しています。\n"
            f"  GitHub Secrets の THREADS_ACCESS_TOKEN を更新してください。"
        )
        sys.exit(2)
    # Threads API は一部の認証エラーを 200 ではなく error.code=190 で返す
    try:
        err = resp.json().get("error", {})
        if err.get("code") == 190:
            print(
                f"[ERROR] {context}: error.code=190 — トークン失効またはパーミッション不足。\n"
                f"  GitHub Secrets の THREADS_ACCESS_TOKEN を更新してください。\n"
                f"  詳細: {err.get('message', '')}"
            )
            sys.exit(2)
    except Exception:
        pass


def create_container(text: str, reply_to_id: str | None = None) -> str | None:
    data = {"media_type": "TEXT", "text": text, "access_token": ACCESS_TOKEN}
    if reply_to_id:
        data["reply_to_id"] = reply_to_id
    resp = requests.post(
        f"{API}/{USER_ID}/threads", data=data, timeout=30
    )
    if resp.status_code != 200:
        _check_token_error(resp, "コンテナ作成")
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
        _check_token_error(resp, "投稿公開")
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
- slot: {SLOT}
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

    # 1. スロットガード（[POST] / [CLAIM] 両方を確認）
    if already_posted_today(SLOT):
        print(f"[スキップ] slot:{SLOT} は本日投稿済みまたは確保済み。終了。")
        sys.exit(0)

    # 2. スロット確保（TOCTOU 対策）― API 呼び出し前に [CLAIM] を書き込む
    if not claim_slot(SLOT):
        print(f"[スキップ] slot:{SLOT} のスロット確保に失敗。別ジョブが先着。終了。")
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

    # 9. ファイル更新（[CLAIM] は [POST] の append_log が事実上上書きする）
    update_queue_status(post["id"])
    append_log(post, post_id)
    append_history(post, post_id, reply_id)

    print(f"=== 完了 {jst_now().strftime('%Y-%m-%d %H:%M JST')} ===")


if __name__ == "__main__":
    main()
