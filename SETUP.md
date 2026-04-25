# Threads 自動運用 クラウド版 セットアップ手順

## 必要なもの
- GitHubアカウント（無料）
- Anthropic APIキー（[console.anthropic.com](https://console.anthropic.com) で取得）
- Threads アクセストークン（Threads Developer で取得）

---

## 手順1: GitHubリポジトリを作成する

1. https://github.com/new を開く
2. Repository name: `threads-cloud`（任意）
3. **Private** を選択
4. 「Create repository」をクリック

---

## 手順2: このフォルダをGitHubにプッシュする

PowerShellまたはコマンドプロンプトで:

```powershell
cd C:\Users\tasuku\threads-cloud

git init
git add .
git commit -m "initial: Threads自動運用システム"
git branch -M main
git remote add origin https://github.com/あなたのユーザー名/threads-cloud.git
git push -u origin main
```

---

## 手順3: GitHub Secretsを設定する

リポジトリページ → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret名 | 値 |
|---------|---|
| `THREADS_ACCESS_TOKEN` | Threads Developer で取得したアクセストークン |
| `THREADS_USER_ID` | Threads ユーザーID（数値） |
| `ANTHROPIC_API_KEY` | Anthropic コンソールで取得した API キー |

---

## 手順4: data/ ディレクトリを初期化する

初回セットアップ時は、必要なファイルを作成します（ローカルで実行）:

```powershell
cd C:\Users\tasuku\threads-cloud
python scripts/init_data.py
git add data/
git commit -m "init: data ディレクトリ初期化"
git push origin main
```

---

## 手順5: Actionsを有効化する

1. リポジトリページ → **Actions** タブ
2. 「I understand my workflows, go ahead and enable them」をクリック

---

## 手順6: 動作確認（手動実行）

**投稿生成（週次）**:
1. Actions → **Threads 週次ジョブ（リサーチ＋投稿生成）** → **Run workflow**
2. ログを確認し、`data/post_queue.md` に投稿が追加されていることを確認

**手動投稿テスト**:
1. Actions → **Threads 投稿 朝（08:00±30分）** → **Run workflow**
2. ログを確認してエラーがないことを確認
3. キューが空だと「投稿キューが空です」で正常終了

---

## ワークフロー一覧

| ファイル | 実行時刻（JST） | 内容 |
|--------|--------------|------|
| `post_morning.yml` | 07:30〜08:30 | 朝スロット投稿（1件） |
| `post_evening1.yml` | 18:30〜19:30 | 夜1スロット投稿（1件） |
| `post_evening2.yml` | 21:00〜22:00 | 夜2スロット投稿（1件） |
| `metrics.yml` | 毎日 22:00 | エンゲージメント取得 + パフォーマンス分析 |
| `weekly.yml` | 月曜 07:00 / キュー5件以下で毎日 | 投稿15件生成（Claude Haiku） |

> **1日最大3件投稿**（morning / evening1 / evening2）。  
> スロットガードにより同一スロットの二重投稿は自動防止されます。

---

## データファイル構成

| ファイル | 用途 |
|--------|------|
| `data/post_queue.md` | 投稿キュー（未投稿の原稿） |
| `data/post_log.md` | 投稿ログ（スロット・日時記録） |
| `data/post-history.md` | 投稿履歴 + メトリクス蓄積 |
| `data/performance_summary.md` | 週次パフォーマンスサマリー（自動生成） |
| `data/reply_insights.md` | フォロワーリプライ蓄積（生成プロンプト参照） |
| `data/generation_log.md` | 週次生成の実行ログ（冪等性チェック用） |
| `data/next-topics.md` | 次回テーマ候補（任意追記） |

---

## トークン更新について

Threads アクセストークンは**60日**で期限切れになります。

期限切れが近づいたら:
1. [Threads Developer](https://developers.facebook.com/apps/) でトークンを更新
2. GitHub Secrets の `THREADS_ACCESS_TOKEN` を新しい値で更新

> トークン切れを post_one.py が検知すると `[ERROR] 401 Unauthorized` を出力して終了します（ファイルは更新されません）。

---

## 費用目安

| サービス | 費用 |
|--------|------|
| GitHub Actions | **$0**（無料枠内） |
| Anthropic API | **$1〜2/月**（Claude Haiku 週1回生成） |
