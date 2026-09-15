#!/usr/bin/env bash
# 過去の有報をまとめて取り込み、A層の検証をやり直すまでを通しで回す。
# 途中で止めても、もう一度実行すれば続きから再開する。
__main_body() {
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
STATE="data/.backfill_state"
mkdir -p logs data
step() {
  if grep -qx "$1" "$STATE" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] 済み: $1"
    return 0
  fi
  echo "[$(date +%H:%M:%S)] ▶ $1"
  shift
  if "$@"; then
    echo "$STEP_NAME" >> "$STATE"
    return 0
  fi
  echo "[$(date +%H:%M:%S)] ⚠ 失敗したので中断（もう一度実行すれば続きから）"
  return 1
}

run() {
  STEP_NAME="$1"; shift
  if grep -qx "$STEP_NAME" "$STATE" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] 済み: $STEP_NAME"
    return 0
  fi
  echo "[$(date +%H:%M:%S)] ▶ $STEP_NAME"
  if "$@"; then
    echo "$STEP_NAME" >> "$STATE"
  else
    echo "[$(date +%H:%M:%S)] ⚠ $STEP_NAME で失敗。もう一度実行すれば続きから再開します"
    exit 1
  fi
}

echo "=================================================="
echo "  過去の有報を取り込んで、A層の検証をやり直す"
echo "=================================================="

run "index_2018" $PY scripts/fetch_edinet_history.py --year 2018 --index
run "fetch_2018" $PY scripts/fetch_edinet_history.py --year 2018 --fetch
run "index_2022" $PY scripts/fetch_edinet_history.py --year 2022 --index
run "fetch_2022" $PY scripts/fetch_edinet_history.py --year 2022 --fetch

echo ""
echo "[$(date +%H:%M:%S)] ▶ 取り込み結果"
$PY - <<'PYEOF'
import sys; sys.path.insert(0, ".")
from modules.store import read_df
e = read_df("SELECT fiscal_year, COUNT(DISTINCT ticker) n, "
            "SUM(CASE WHEN eps IS NOT NULL THEN 1 ELSE 0 END) eps, "
            "SUM(CASE WHEN operating_cf IS NOT NULL THEN 1 ELSE 0 END) ocf "
            "FROM edinet_summary GROUP BY fiscal_year ORDER BY fiscal_year")
print(e.to_string(index=False))
PYEOF

echo ""
echo "[$(date +%H:%M:%S)] ✅ 取り込み完了。A層の検証をやり直せます:"
echo "     .venv/bin/python scripts/validate_capacity.py"
}
__main_body "$@"
