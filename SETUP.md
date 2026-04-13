# Threads 自動運用 クラウド版 セットアップ手順

## 必要なもの
- GitHubアカウント（無料）
- Anthropic APIキー（[console.anthropic.com](https://console.anthropic.com) で取得）
- Threads アクセストークン（既存の `.env` から）

---

## 手順1: GitHubリポジトリを作成する

1. https://github.com/new を開く
2. Repository name: `threads-automation`（任意）
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
git remote add origin https://github.com/あなたのユーザー名/threads-automation.git
git push -u origin main
```

---

## 手順3: GitHub Secretsを設定する

リポジトリページ → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret名 | 値 |
|---------|---|
| `THREADS_ACCESS_TOKEN` | `THAF6hSLBcd5VBUVI2...`（.envのトークン） |
| `THREADS_USER_ID` | `26513283268330100` |
| `ANTHROPIC_API_KEY` | Anthropicコンソールで取得したAPIキー |

---

## 手順4: Actionsを有効化する

1. リポジトリページ → **Actions** タブ
2. 「I understand my workflows, go ahead and enable them」をクリック

---

## 手順5: 動作確認（手動実行）

1. Actions → **Threads 投稿（1日10スロット）** → **Run workflow**
2. ログを確認してエラーがないことを確認

---

## スケジュール

| ワークフロー | 実行時刻（JST） | 内容 |
|------------|--------------|------|
| post.yml | 07:18, 08:45, 10:23, 12:07, 13:41, 15:28, 17:12, 19:35, 21:08, 22:43 | 1日5〜10件投稿 |
| weekly.yml | 月曜 07:00 | リサーチ＋投稿15件生成 |
| metrics.yml | 毎日 21:00 | エンゲージメント取得 |

---

## トークン更新について

Threads アクセストークンは60日で期限切れになります。
`config/refresh_token.py` を手動で実行してトークンを更新し、
GitHub Secrets の `THREADS_ACCESS_TOKEN` を更新してください。

---

## 費用目安

- GitHub Actions: **$0**（無料枠内）
- Anthropic API: **$1〜2/月**（Claude Haiku使用、週1回生成）
