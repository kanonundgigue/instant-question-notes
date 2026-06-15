#!/usr/bin/env sh
set -eu

# ファイル削除が必要になったら、人間の確認用に削除コマンドをここへ追記する。

# PPTX生成ランタイムがリポジトリ直下に作成した一時的な Node 設定ファイル。プロジェクト本体では不要。
rm -f package.json

# 保護記事にPPTXを埋め込んだ後に残った生成作業ディレクトリ。最終公開物には不要。
rm -rf outputs/manual-20260614-jspo-basic-presentation
