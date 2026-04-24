"""
共通ユーティリティ
- 時刻・パス定数
- post_queue.md パーサー（単一実装）
- キュー更新（安全な正規表現）
- atomic_write（クラッシュ耐性のあるファイル書き込み）
"""

import re
import os
import shutil
import tempfile
import datetime
from pathlib import Path

# ─── 時刻 ────────────────────────────────────────────────────
JST = datetime.timezone(datetime.timedelta(hours=9))


def jst_now() -> datetime.datetime:
    return datetime.datetime.now(JST)


# ─── パス定数 ─────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
DATA_DIR      = BASE_DIR / "data"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
QUEUE_PATH    = DATA_DIR / "post_queue.md"
LOG_PATH      = DATA_DIR / "post_log.md"
HISTORY_PATH  = DATA_DIR / "post-history.md"


# ─── キューパーサー（単一実装・全スクリプト共用）────────────────
_QUEUE_PATTERN = re.compile(
    r"---\n"
    r"id:\s*(?P<id>[^\n]+)\n"
    r"type:\s*(?P<type>[^\n]+)\n"
    r"status:\s*queued\n"
    r"source:\s*(?P<source>[^\n]+)\n"
    r"topic:\s*(?P<topic>[^\n]+)\n"
    r"(?:score_target:\s*[^\n]+\n)?"
    r"created:\s*(?P<created>[^\n]+)\n"
    r"---\n\n"
    r"(?P<body_block>.*?)(?=\n\n---|\Z)",
    re.DOTALL,
)


def parse_queue(text: str | None = None) -> list[dict]:
    """status: queued のエントリを全件パースして返す。"""
    if text is None:
        if not QUEUE_PATH.exists():
            return []
        text = QUEUE_PATH.read_text(encoding="utf-8")

    posts = []
    for m in _QUEUE_PATTERN.finditer(text):
        body_block = m.group("body_block")

        # セルフリプライを HTML コメントから抽出
        reply_match = re.search(
            r"<!--\s*self_reply:\n(.*?)\n-->", body_block, re.DOTALL
        )
        self_reply = reply_match.group(1).strip() if reply_match else ""

        # 本文からコメントブロックを除去
        body = re.sub(
            r"\n*<!--\s*self_reply:.*?-->", "", body_block, flags=re.DOTALL
        ).strip()

        posts.append(
            {
                "id":         m.group("id").strip(),
                "type":       m.group("type").strip(),
                "source":     m.group("source").strip(),
                "topic":      m.group("topic").strip(),
                "created":    m.group("created").strip(),
                "body":       body,
                "self_reply": self_reply,
            }
        )
    return posts


# ─── 原子的ファイル書き込み ────────────────────────────────────
def atomic_write(path: Path, content: str) -> None:
    """tempfile に書いて rename で入れ替える（POSIX 原子操作）。
    途中クラッシュでもファイルが中途半端な状態にならない。
    """
    dir_ = path.parent
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
    ) as tf:
        tf.write(content)
        tmp_path = tf.name
    shutil.move(tmp_path, str(path))


# ─── キュー更新（安全版）─────────────────────────────────────
def update_queue_status(post_id: str) -> None:
    """指定 ID の status を queued → posted に変更する。
    DOTALL を使わず [^\\n]+ で行内マッチに限定し、誤マッチを防ぐ。
    regex がマッチしない場合は [WARN] を出す（サイレント失敗を防止）。
    """
    content = QUEUE_PATH.read_text(encoding="utf-8")
    updated = re.sub(
        r"(?m)^(id:\s*"
        + re.escape(post_id)
        + r"\ntype:\s*[^\n]+\nstatus:\s*)queued",
        r"\g<1>posted",
        content,
    )
    if updated == content:
        print(f"[WARN] update_queue_status: ID '{post_id}' にマッチせず。ステータス未更新。")
    atomic_write(QUEUE_PATH, updated)
