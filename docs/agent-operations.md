# エージェント運用詳細

この文書は `AGENTS.md` の補足です。エージェントが毎回読む入口は短い `AGENTS.md` とし、背景や細かい判断基準が必要なときだけこちらを参照します。

## リポジトリ構成

```text
instant-question-notes/
├── index.html
├── styles/
│   └── main.css
├── scripts/
│   ├── code-copy.js
│   ├── crypto-core.js
│   └── decrypt.js
├── tools/
│   ├── encrypt.html
│   └── encrypt.js
├── images/
├── notes/
│   ├── _template/
│   │   ├── article.html
│   │   └── article-protected.html
│   ├── private/
│   │   └── index.html
│   └── <category>/
│       ├── index.html
│       └── <slug>.html
├── .github/
│   └── workflows/
│       └── pages.yml
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── plan.md
├── cleanup.sh
└── .gitignore
```

## 依頼の省略ルール

このリポジトリでは、ユーザーが疑問や調べたいことだけを送った場合、特に断りがなければ記事作成依頼として扱います。短い回答で済む質問でも、公開ノートとして残せる内容なら記事化します。

対象例:

- `uv.lock はいつ更新される？`
- `GitHub Actions の permissions は何を指定すべき？`
- `Python の pathlib.Path と os.path はどう使い分ける？`
- `引っ越し前に住所変更が必要なサービスは？`
- `このプロジェクトの使い方は？`
- `Codex にどう依頼すれば記事になる？`

記事化しない判断をするのは、ユーザーが明示的に記事化しない意図を示した場合、または機密情報・個人情報・未公開情報を含み公開ノートにできない場合に限ります。

## カテゴリとスラッグ

カテゴリ名と記事ファイル名は英小文字ケバブケースにします。

| 対象 | 形式 | 例 |
|---|---|---|
| カテゴリ名 | 英小文字ケバブケース | `python`, `git`, `climate-data` |
| 記事ファイル名 | 英小文字ケバブケース + `.html` | `uv-lockfile-basics.html` |

既存カテゴリに無理に寄せず、必要なら新しいカテゴリを作ってよいです。新カテゴリを作る場合は、`index.html` にページ内アンカー付きのカテゴリ導線とカテゴリ別セクションを追加します。

音楽関係の質問は、初心者向けの入門解説ではなく、プロが知識を整理するための質問として扱います。

## index.html 更新

記事追加または既存記事更新時は、`index.html` だけで記事を探せるように更新します。トップページはページ内アンカーで移動する「1 枚看板」として扱い、カテゴリ別ページへの遷移を主要導線にしません。

更新対象:

- ヘッダーまたはカテゴリ一覧のページ内アンカー
- 新着セクション
- 該当カテゴリのカテゴリ別セクション

記事カードに含める情報:

- カテゴリラベル
- 記事タイトル
- 作成日
- 更新日
- 記事へのリンク

既存の `notes/<category>/index.html` は過去の URL 互換のため残します。ただし、新しい記事追加時にカテゴリページを追加または更新することは必須ではありません。

## 保護記事の詳細

保護記事は「コンテンツを暗号化して公開し、ブラウザ内でパスワード復号する」方式です。GitHub Pages はサーバー側処理を持たないため本物のログイン認証はできませんが、この方式なら公開される本文は暗号文だけになります。

実装はブラウザ標準の Web Crypto API を使い、`scripts/crypto-core.js` と `scripts/decrypt.js` が復号側の共通ロジックです。外部ライブラリやビルドツールは導入しません。

保護記事作成時の注意:

- 公開される情報は、記事タイトル、要約、URL、ページ `<title>` です。ここに秘密を書かない
- 全保護記事で共通の 1 つのパスワードを使う
- 共通パスワードは `PROTECTED_NOTES.local.md` に置き、`.gitignore` 済みとする
- `tools/encrypt.html` はローカル専用で、GitHub Pages の公開対象に含めない
- Web Crypto とローカルメモ読込の都合上、暗号化と復号確認は `file://` ではなく localhost で行う
- プレビューサーバを立てる場合は、可能なら公開対象だけを配信し、`PROTECTED_NOTES.local.md` を不用意に露出させない

## デプロイ対象

GitHub Pages で公開されるのは `.github/workflows/pages.yml` でステージングされたファイルだけです。

公開対象:

- `index.html`
- `styles/`
- `scripts/`
- `images/`
- `notes/`（ただし `notes/_template/` は除外）

公開しないもの:

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `plan.md`
- `cleanup.sh`
- `docs/`
- `tools/`
- `PROTECTED_NOTES.local.md`
- 下書きや生成作業ディレクトリ

新しく公開したい種類のファイルを増やす場合は、ワークフローのステージング手順にも追記します。

## GitHub リポジトリ設定

このリポジトリは個人用ノートとして、所有者だけが更新できる状態で運用します。

- GitHub の `Settings` > `Collaborators & teams` で、所有者以外に write / maintain / admin 権限を持つユーザーやチームを置かない
- public repository でも、collaborator でない第三者は通常 push できない
- `main` ブランチには直接 push できる状態にする
- branch protection rule / ruleset で直接 push を妨げる設定がある場合は無効化する
- push できる人を制限する ruleset を使う場合は、所有者または信頼済みの自動化だけを許可する

## Codex で外出先から使う場合

1. ChatGPT アプリで Codex を開き、このリポジトリを選ぶ
2. 「質問〜 ◯◯について記事にして」と渡す
3. Codex が `AGENTS.md` に従って記事を作る
4. 公開まで任せる場合は「pushして」または「公開して」と明示する
5. Codex が `main` へ直接 push し、GitHub Pages が公開される
