#!/usr/bin/env sh
set -eu

# ファイル削除が必要になったら、人間の確認用に削除コマンドをここへ追記する。

# package.json は公開前チェックの npm run check 入口として使うため削除しない。

# 保護記事にPPTXを埋め込んだ後に残った生成作業ディレクトリ。最終公開物には不要。
rm -rf outputs/manual-20260614-jspo-basic-presentation
