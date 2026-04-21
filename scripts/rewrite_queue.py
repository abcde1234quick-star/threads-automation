"""
キュー内の未投稿（status: queued）を10テンプレートで書き直す。
1件ずつ処理して確実に全件書き直す。
"""
import os, re, sys, time
import anthropic
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / "data"
QUEUE_PATH = DATA_DIR / "post_queue.md"

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ─── テンプレート定義 ──────────────────────────────────────
TEMPLATES = """\
【テンプレ1】やりすぎNG逆張り型（驚き・比較）
実は【○○】やりすぎると逆効果だった…
✗ やってた：毎日○○
✓ 正解：○○は週○回まで
この差で肌の○○が全然違う
知らなかった人はぜひ試してみて🙏

【テンプレ2】わかる！共感爆発型（共感・悩み）
○○に悩んでる人、絶対共感するやつ↓
「○○してるのになんか違う…」
「なんか老けて見える気がする」
「○○試したけど続かない」
全部あてはまってた私が唯一続いたのが○○です

【テンプレ3】知らなかった情報差型（教育・知識）
美容部員に聞いたら教えてもらえた話
○○の正しい使い方、みんなに知ってほしいんだけど
①○○
②○○
③○○
これだけで○○が変わります
保存しておいて損なし📎

【テンプレ4】before/after変化実感型（驚き・比較）
○○を始めて○ヶ月。正直に報告します
【1ヶ月目】○○
【2ヶ月目】○○
【3ヶ月目】○○←ここで変化を実感
使ったのは○○だけ。コスパも◎
続けてよかった…🥺

【テンプレ5】あなたのタイプは？診断型（共感・悩み）
肌タイプ別・おすすめ○○まとめました
🔵 乾燥肌さんには→○○
🟡 混合肌さんには→○○
🔴 敏感肌さんには→○○
⚪ 普通肌さんには→○○
自分のタイプはどれ？コメントで教えて！

【テンプレ6】コスパ最強プチプラ暴露型（行動誘発）
デパコス愛用者の私が言う「プチプラで十分」なアイテム3選
①○○（¥○○）→○○と同じ使い心地
②○○（¥○○）→○○の代わりに
③○○（¥○○）→むしろこっちの方が好き
お金は大事なところに使おう🤍

【テンプレ7】今すぐやめて警告型（行動誘発）
悪化するだけだから今すぐやめて🚨
✗ ○○しながらスキンケア
✗ ○○の順番が逆
✗ 寝る前に○○を使う
これ全部、肌への負担が大きい
知らずにやってた人は今日から変えてみて

【テンプレ8】○○円以下縛り挑戦型（行動誘発）
スキンケア全部○○円以下に統一してみた話
洗顔：○○（¥○○）
化粧水：○○（¥○○）
乳液：○○（¥○○）
UVケア：○○（¥○○）
合計○○円で肌の調子がむしろよくなった件

【テンプレ9】この成分だけ見て成分解説型（教育・知識）
コスメ買うとき成分表の「○○」だけ見てれば正直OK
◎ ○○が入ってる→買い
△ ○○のみ→普通
✗ ○○がある→○○肌は避けて
難しく考えなくていい
これだけ覚えれば失敗しなくなるよ📝

【テンプレ10】○代の私へタイムスリップ型（共感・悩み）
20代の自分に教えてあげたいこと（美容編）
「○○はそんなにいらない」
「○○だけやれば十分だった」
「○○に早く出会いたかった」
今の○○代の子たちに届いてほしい🙏
"""

SINGLE_PROMPT = """\
あなたはThreads美容アカウント（@mao3.575）の投稿ライターです。
以下のトピック1件について、最も合うテンプレートを選んで本文とセルフリプライを書いてください。

## テンプレート一覧
{templates}

## 書き直し対象
トピック: {topic}
現在の本文: {body}

## 出力ルール
- 「〜です」「〜ます」禁止、フラットな話し言葉
- 「〜ですね」「〜でしょう」「まとめると」など AI感ある表現禁止
- 断定禁止、「〜かもしれない」「気がした」など仮説・体験として書く
- 本文: 200〜350字、改行多用
- セルフリプライ: 100〜200字（本文で言い切れなかった補足・手順・豆知識）
- テンプレートの骨格を維持しつつ○○をトピックに合わせ具体的に埋める

## 出力フォーマット（このフォーマットのみ出力すること）
TEMPLATE: テンプレ番号（テンプレート名）
BODY:
（書き直した本文）
SELF_REPLY:
（書き直したセルフリプライ）
"""

# ─── キュー読み込み ────────────────────────────────────────
def parse_queued(text: str) -> list[dict]:
    pattern = re.compile(
        r'---\n'
        r'id:\s*(.+?)\n'
        r'type:\s*(.+?)\n'
        r'status:\s*queued\n'
        r'source:\s*(.+?)\n'
        r'topic:\s*(.+?)\n'
        r'(?:score_target:\s*.+?\n)?'
        r'created:\s*(.+?)\n'
        r'---\n\n'
        r'(.*?)(?=\n\n---|\Z)',
        re.DOTALL
    )
    posts = []
    for m in pattern.finditer(text):
        body_block = m.group(5)
        reply_match = re.search(r'<!--\s*self_reply:\n(.*?)\n-->', body_block, re.DOTALL)
        self_reply = reply_match.group(1).strip() if reply_match else ""
        body = re.sub(r'\n*<!--\s*self_reply:.*?-->', '', body_block, flags=re.DOTALL).strip()
        posts.append({
            "id":      m.group(1).strip(),
            "type":    m.group(2).strip(),
            "source":  m.group(3).strip(),
            "topic":   m.group(4).strip(),
            "created": m.group(4+1-1+1).strip(),  # group(5) は body なので group index ずれ補正
            "body":    body,
            "self_reply": self_reply,
        })
    # created を別途取得
    for i, m in enumerate(pattern.finditer(text)):
        posts[i]["created"] = m.group(4).strip()  # topic
    # 再パースして created を正しく取得
    posts2 = []
    pat2 = re.compile(
        r'---\n'
        r'id:\s*(?P<id>.+?)\n'
        r'type:\s*(?P<type>.+?)\n'
        r'status:\s*queued\n'
        r'source:\s*(?P<source>.+?)\n'
        r'topic:\s*(?P<topic>.+?)\n'
        r'(?:score_target:\s*.+?\n)?'
        r'created:\s*(?P<created>.+?)\n'
        r'---\n\n'
        r'(?P<body_block>.*?)(?=\n\n---|\Z)',
        re.DOTALL
    )
    for m in pat2.finditer(text):
        body_block = m.group('body_block')
        reply_match = re.search(r'<!--\s*self_reply:\n(.*?)\n-->', body_block, re.DOTALL)
        self_reply = reply_match.group(1).strip() if reply_match else ""
        body = re.sub(r'\n*<!--\s*self_reply:.*?-->', '', body_block, flags=re.DOTALL).strip()
        posts2.append({
            "id":         m.group('id').strip(),
            "type":       m.group('type').strip(),
            "source":     m.group('source').strip(),
            "topic":      m.group('topic').strip(),
            "created":    m.group('created').strip(),
            "body":       body,
            "self_reply": self_reply,
        })
    return posts2


# ─── 1件書き直し ──────────────────────────────────────────
def rewrite_one(post: dict) -> dict | None:
    prompt = SINGLE_PROMPT.format(
        templates=TEMPLATES,
        topic=post["topic"],
        body=post["body"][:300],
    )
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text
            template_m = re.search(r'TEMPLATE:\s*(.+)', raw)
            body_m     = re.search(r'BODY:\n(.*?)(?=SELF_REPLY:|\Z)', raw, re.DOTALL)
            reply_m    = re.search(r'SELF_REPLY:\n(.*?)$', raw, re.DOTALL)
            if not body_m:
                print(f"    [retry {attempt+1}] BODYが見つかりません")
                time.sleep(2)
                continue
            return {
                "template":   template_m.group(1).strip() if template_m else "",
                "body":       body_m.group(1).strip(),
                "self_reply": reply_m.group(1).strip() if reply_m else "",
            }
        except Exception as e:
            print(f"    [retry {attempt+1}] エラー: {e}")
            time.sleep(5)
    return None


# ─── キューファイル更新 ────────────────────────────────────
def update_one_post(text: str, post: dict, rw: dict) -> str:
    new_topic = post["topic"].split(" [")[0]  # 既存テンプレ表記を除去
    if rw["template"]:
        new_topic = f"{new_topic} [{rw['template']}]"

    new_body_block = rw["body"]
    if rw["self_reply"]:
        new_body_block += f"\n\n<!-- self_reply:\n{rw['self_reply']}\n-->"

    pid = re.escape(post["id"])
    block_pat = re.compile(
        r'---\n'
        r'id:\s*' + pid + r'\n'
        r'type:\s*(?P<type>\S+)\n'
        r'status:\s*queued\n'
        r'source:\s*(?P<source>\S+)\n'
        r'topic:\s*.+?\n'
        r'(?:score_target:\s*.+?\n)?'
        r'created:\s*(?P<created>.+?)\n'
        r'---\n\n'
        r'(?P<body>.*?)(?=\n\n---|\Z)',
        re.DOTALL
    )

    def replacer(m):
        new_header = (
            f"---\n"
            f"id: {post['id']}\n"
            f"type: {m.group('type')}\n"
            f"status: queued\n"
            f"source: {m.group('source')}\n"
            f"topic: {new_topic}\n"
            f"created: {m.group('created')}\n"
            f"---\n\n"
        )
        return new_header + new_body_block

    new_text, n = block_pat.subn(replacer, text, count=1)
    if n == 0:
        print(f"    [WARN] ID:{post['id']} パターン不一致、スキップ")
    return new_text


# ─── メイン ───────────────────────────────────────────────
def main():
    print("=== rewrite_queue.py 開始 ===\n")

    text = QUEUE_PATH.read_text(encoding='utf-8')
    posts = parse_queued(text)
    print(f"対象: {len(posts)}件の queued 投稿\n")

    if not posts:
        print("書き直し対象なし。終了。")
        return

    ok, ng = 0, 0
    for i, post in enumerate(posts, 1):
        print(f"[{i:02d}/{len(posts)}] ID:{post['id']} | {post['topic'][:35]}...")
        rw = rewrite_one(post)
        if rw:
            text = update_one_post(text, post, rw)
            print(f"         ✓ {rw['template']}")
            ok += 1
        else:
            print(f"         ✗ 失敗")
            ng += 1
        time.sleep(1)  # API レート制限対策

    QUEUE_PATH.write_text(text, encoding='utf-8')
    print(f"\n=== 完了 | 成功:{ok}件 / 失敗:{ng}件 ===")


if __name__ == "__main__":
    main()
