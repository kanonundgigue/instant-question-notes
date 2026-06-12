# instant-question-notes

技術的な疑問を、メモる速度で静的 HTML の記事として蓄積する個人用ノートです。

## 使い方

1. 質問を Claude Code / Codex に渡す
2. エージェントが一次情報を調査して `notes/<category>/<slug>.html` を作成する
3. `index.html` の記事カードを日付降順で更新する
4. 人間が差分を確認してからコミット・プッシュする

このリポジトリでは、技術的な疑問だけを送れば記事化の依頼として扱います。毎回「これを instant-question-notes の記事にして」と書く必要はありません。

```text
uv.lock はいつ更新される？
GitHub Actions の permissions は何を指定すべき？
Python の pathlib.Path と os.path はどう使い分ける？
```

記事にせず会話だけしたい場合は、「説明だけ」「記事にしないで」のように明示します。

ビルド工程はありません。`index.html` をブラウザで開けば閲覧できます。

## 構成

```text
instant-question-notes/
├── index.html
├── styles/
│   └── main.css
├── images/
├── notes/
│   └── _template/
│       └── article.html
├── .github/
│   └── workflows/
│       └── pages.yml
├── AGENTS.md
├── CLAUDE.md
├── cleanup.sh
└── plan.md
```

## 記事作成ルール

- 日本語で書く
- 1 ページ 1 テーマにする
- 元の質問文を冒頭に残す
- 推測は「推測」と明示する
- 記事末尾に一次情報の参照 URL を必ず置く
- 記事固有 CSS は原則書かず、`styles/main.css` を使う
