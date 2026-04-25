"""
data/ ディレクトリと必要な空ファイルを初期化するスクリプト。
初回セットアップ時や data/ が消えた場合に実行する。

使い方:
  python scripts/init_data.py
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

# 必須ファイルとデフォルト内容
REQUIRED_FILES: dict[str, str] = {
    "post_queue.md":   "# 投稿キュー\n\n",
    "post_log.md":     "# Threads 投稿ログ\n\n",
    "post-history.md": "# 投稿履歴\n\n",
}

# 任意ファイル（なくても動くが、あると便利）
OPTIONAL_FILES: dict[str, str] = {
    "next-topics.md":        "# 次回テーマ候補\n\n",
    "reply_insights.md":     "# リプライインサイト\n\n",
    "trend_history.md":      "# トレンド履歴\n\n",
    "generation_log.md":     "# 週次生成ログ\n\n",
}


def main() -> None:
    print(f"=== init_data.py 開始 ===")

    # data/ ディレクトリを作成
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[OK] data/ ディレクトリ: {DATA_DIR}")

    # 必須ファイルを作成
    print("\n[必須ファイル]")
    for fname, default_content in REQUIRED_FILES.items():
        path = DATA_DIR / fname
        if path.exists():
            print(f"  [skip] {fname} は既に存在します")
        else:
            path.write_text(default_content, encoding="utf-8")
            print(f"  [作成] {fname}")

    # 任意ファイルを作成
    print("\n[任意ファイル]")
    for fname, default_content in OPTIONAL_FILES.items():
        path = DATA_DIR / fname
        if path.exists():
            print(f"  [skip] {fname} は既に存在します")
        else:
            path.write_text(default_content, encoding="utf-8")
            print(f"  [作成] {fname}")

    print("\n=== 完了 ===")


if __name__ == "__main__":
    main()
