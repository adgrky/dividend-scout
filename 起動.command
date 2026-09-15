#!/usr/bin/env bash
# dividend-scout 起動スクリプト
# Finder でダブルクリックすると、ターミナルが開いてアプリがブラウザで立ち上がります。
# 閉じるときは、ターミナルの画面で Control + C。

set -e
cd "$(dirname "$0")"

echo "🔭 dividend-scout"

if [ ! -d ".venv" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "⚠ uv が見つかりません。先にこれを実行してください:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    read -n 1 -s -r -p "Enter キーでウィンドウを閉じます…"
    exit 1
  fi
  echo "▶ 初回起動: 環境を作っています（数分かかります）…"
  uv venv --python 3.11
  uv pip install -r requirements.txt
  shasum requirements.txt > .venv/.reqs.sha
else
  # 毎回 uv を呼ぶと起動が遅くなるので、requirements.txt が変わったときだけ入れ直す
  if ! shasum -c .venv/.reqs.sha >/dev/null 2>&1; then
    echo "▶ 必要なものを更新しています…"
    uv pip install -r requirements.txt
    shasum requirements.txt > .venv/.reqs.sha
  fi
fi

if [ ! -f "data/scout.db" ]; then
  echo ""
  echo "⚠ データがまだありません。先に全市場スキャンが必要です（約15分）:"
  echo "    uv run python scripts/weekly_scan.py"
  echo ""
  read -n 1 -s -r -p "Enter キーでウィンドウを閉じます…"
  exit 1
fi

echo "▶ ブラウザが開きます。終了するには Control + C"
echo ""

exec .venv/bin/streamlit run app.py --server.port 8502
