#!/usr/bin/env bash
# dividend-scout 起動スクリプト
# Finder でダブルクリックすると、ターミナルが開いてアプリがブラウザで立ち上がります。
# 閉じるときは、ターミナルの画面で Control + C。

set -e
cd "$(dirname "$0")"

echo "==========================================="
echo "  🔭 dividend-scout 起動中…"
echo "==========================================="
echo ""

if ! command -v uv >/dev/null 2>&1; then
  echo "⚠ uv が見つかりません。先にこれを実行してください:"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  read -n 1 -s -r -p "Enter キーでウィンドウを閉じます…"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "▶ 初回起動: 環境を作っています（数分かかります）…"
  uv venv --python 3.11
  uv pip install -r requirements.txt
else
  uv pip install -q -r requirements.txt 2>/dev/null || true
fi

if [ ! -f "data/scout.db" ]; then
  echo ""
  echo "⚠ データがまだありません。先に全市場スキャンが必要です（約15分）:"
  echo "    uv run python scripts/weekly_scan.py"
  echo ""
  read -n 1 -s -r -p "Enter キーでウィンドウを閉じます…"
  exit 1
fi

echo ""
echo "▶ ブラウザが自動で開きます"
echo "  終了するには、この画面で Control + C"
echo ""

exec .venv/bin/streamlit run app.py --server.port 8502
