# Strategy

## 投稿評価式（config.py の calc_score() と完全一致）

score =
  views   × 1 +
  likes   × 2 +
  replies × 3 +
  reposts × 2 +
  quotes  × 2

※ saves は Threads API 非公開のため除外（取得不可）

## 学習ルール

score 上位投稿の「構造・トーン・フック種別」を再利用。
数値ではなく「なぜ止まったか・なぜ返信されたか」を分析する。

## 成長段階

比較 → 検証 → 判断代行
