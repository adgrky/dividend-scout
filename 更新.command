#!/usr/bin/env bash
# diva データ更新
#
# Finder でダブルクリックすると、株価・配当・スコアを取り直します。
# アプリを開いたままでも実行できます（終わったらアプリ側で「読み込み直す」を押す）。

# 【実行中に編集されても壊れないようにする】
# bash はスクリプトを「何バイト目まで読んだか」で追いながら実行するので、
# 実行中にファイルを書き換えると、ずれた位置から読み直して同じ行を二度
# 実行することがある（実測: 更新中に編集したら取得が二重に走った）。
# 本体を関数にしておけば、bash は定義を丸ごと読んでから実行するので影響を受けない。
__main_body() {
cd "$(dirname "$0")"

echo ""
echo "=================================================="
echo "  🔄 DIVA データ更新"
echo "=================================================="
echo ""

die() { echo ""; echo "⚠ $1"; echo ""; read -n 1 -s -r -p "Enter キーでこの窓を閉じます…"; exit 1; }
[ -d ".venv" ] || die "環境がまだありません。先に 起動.command を一度実行してください。"
PY=".venv/bin/python"

# ── いま何が古いかを出す ──
$PY - <<'PYEOF'
import sys, sqlite3
from datetime import date
from pathlib import Path
sys.path.insert(0, ".")
from modules.config import db_path
p = db_path()
if not p.exists():
    print("  データがまだありません。初回の取り込みから始めます。")
    raise SystemExit
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
def age(sql):
    try:
        v = c.execute(sql).fetchone()[0]
        if not v:
            return None
        return (date.today() - date.fromisoformat(str(v)[:10])).days
    except Exception:
        return None
rows = [
    ("株価・配当・スコア", age("SELECT MAX(asof) FROM quotes"), 7, "毎週"),
    ("会社の基本情報",     age("SELECT MAX(updated_at) FROM company_profile"), 30, "月1回"),
    ("有価証券報告書",     age("SELECT MAX(fetched_at) FROM edinet_index"), 180, "年1回"),
]
print("  いまのデータの古さ")
print("  " + "-" * 46)
for name, d, limit, freq in rows:
    if d is None:
        print(f"  {name:20s} まだありません        （{freq}）")
    else:
        mark = "⚠️ 古い" if d > limit else "✅ 新しい"
        print(f"  {name:20s} {d:4d} 日前  {mark}   （{freq}）")
print("  " + "-" * 46)
PYEOF

echo ""
echo "  何を更新しますか？"
echo ""
echo "    1) 株価・配当・スコア        約20分  ← ふだんはこれ"
echo "    2) ＋ 会社の基本情報          約30分  （月1回でよい）"
echo "    3) ぜんぶ（有価証券報告書も） 約90分  （年1回でよい）"
echo "    0) やめる"
echo ""
read -r -p "  番号を入れて Enter: " CHOICE
echo ""

case "$CHOICE" in
  1|2|3) ;;
  *) echo "  やめました"; exit 0 ;;
esac

START=$(date +%s)

echo "▶ 外部データ源が生きているか確認中…"
$PY scripts/selfcheck.py || die "外部データ源に問題があります。上のメッセージを確認してください。"

echo ""
echo "▶ 株価・配当・スコアを取り直しています（約20分）…"
echo "  （銘柄ごとの取得は0.4秒ずつ間隔を空けています。回線制限で弾かれないため）"
$PY scripts/weekly_scan.py || die "更新に失敗しました。上のメッセージを確認してください。"

if [ "$CHOICE" = "2" ] || [ "$CHOICE" = "3" ]; then
  echo ""
  echo "▶ 会社の基本情報を取り直しています…"
  $PY scripts/fetch_profiles.py --missing-only || echo "  ⚠ 会社情報の取得に失敗（続行します）"
fi

if [ "$CHOICE" = "3" ]; then
  echo ""
  echo "▶ 有価証券報告書を取り直しています（長くかかります）…"
  $PY scripts/fetch_edinet.py --index --fetch || echo "  ⚠ 有報の取得に失敗（続行します）"
fi

echo ""
echo "▶ 数字の検算をしています…"
$PY scripts/check_schema.py || die "DBの形が合っていません。上のメッセージを確認してください。"
$PY scripts/audit.py | tail -4

END=$(date +%s)
echo ""
echo "=================================================="
echo "  ✅ 更新が終わりました（$(( (END-START)/60 )) 分 $(( (END-START)%60 )) 秒）"
echo ""
echo "  アプリを開いているなら、左下の「読み込み直す」を押すと"
echo "  新しい数字に入れ替わります。"
echo "=================================================="
echo ""
read -n 1 -s -r -p "Enter キーでこの窓を閉じます…"

}

__main_body "$@"
