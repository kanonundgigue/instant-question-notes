# 記事テンプレートの使い方

新規記事では、まず `article.html` を `notes/<category>/<slug>.html` へコピーします。本文に表、コード、手順などが必要な場合だけ、`article-parts.html` から該当部分をコピーします。

```shell
cp notes/_template/article.html notes/category/article-slug.html
```

## 最短手順

1. `article.html` をコピーし、ファイル名を英小文字ケバブケースにする
2. `{{...}}` を記事内容で置き換える
3. 不要な参照や引用を削除し、引用番号を本文で現れる順にそろえる
4. `index-card.html` のカードを `index.html` の新着とカテゴリ別セクションへコピーする
5. 新カテゴリの場合だけ `category-blocks.html` を使う
6. `rg -n '\{\{|\}\}' notes/category/article-slug.html index.html` で置換漏れを確認する
7. `git diff --check` と `npm run check` を実行する

## ファイルの役割

- `article.html`: ほぼ全記事で使う本文の骨格
- `article-parts.html`: 手順、注意点、コード、モバイル対応表、引用の部品集
- `index-card.html`: 新着とカテゴリ別セクションで共用する記事カード
- `category-blocks.html`: 新カテゴリ用の一覧導線とセクション
- `article-protected.html`: 暗号化済み保護記事の外枠

テンプレート内の `{{...}}` は置換必須です。公開対象へ残すと `npm run check` が失敗します。
