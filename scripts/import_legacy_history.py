"""portfolio-manager が貯めていた資産の推移を引き継ぐ。

引き継ぐのは date / 評価額 / 取得額 / 年間配当 / 保有数 の5つだけ。

**この132日ぶんは実測ではなく推計です。** portfolio-manager は当時の保有数を
残していないので、`backfill_history()` が「今の保有数・取得単価のまま過去も
持っていたら評価額はいくらだったか」を過去株価から計算して書いていた
（app.py のコメントに明記されている）。だから取得額・年間配当・保有数は
全期間で同じ値が並ぶ。

一方このアプリの推移は、画面を開いた日の実際の値を1行ずつ残した実測値。
混ぜて1本の線にすると、増えていない配当が増えたように見えてしまうので、
source 列で 'backfill'（推計）と 'snapshot'（実測）を必ず分ける。

税引後の年間配当は元データに無いので空のままにする。当時の口座別の配当内訳が
残っていない以上、特定20.315%／NISA非課税の按分は復元できない。

使い方:
    uv run python scripts/import_legacy_history.py --dry-run
    uv run python scripts/import_legacy_history.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.config import bridge_secrets_to_env, legacy_dir   # noqa: E402
from modules.store import init_db, read_df, upsert_df          # noqa: E402

_COLS = ["date", "total_eval", "total_cost", "annual_dividend",
         "annual_dividend_after_tax", "holdings_count", "yoc", "source"]


def build(src: Path) -> pd.DataFrame:
    path = src / "history.csv"
    if not path.exists():
        print(f"  見つかりません: {path}")
        return pd.DataFrame(columns=_COLS)

    df = pd.read_csv(path)
    print(f"  読み込み: {path.name}（{len(df)} 行）")

    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "total_eval": df["total_eval"].astype(float),
        "total_cost": df["total_cost"].astype(float),
        "annual_dividend": df["total_dividend"].astype(float),
        "annual_dividend_after_tax": pd.NA,
        "holdings_count": df["holdings_count"].astype(int),
    })
    out["yoc"] = (out["annual_dividend"] / out["total_cost"]).where(out["total_cost"] > 0)
    out["source"] = "backfill"
    return out.reindex(columns=_COLS).drop_duplicates(subset=["date"], keep="last")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="書き込まずに内容だけ出す")
    args = ap.parse_args()

    bridge_secrets_to_env()
    init_db()

    src = legacy_dir()
    print(f"引き継ぎ元: {src}")
    new = build(src)
    if new.empty:
        print("引き継ぐものがありませんでした。")
        return

    existing = read_df("SELECT date, source FROM equity_history")
    have = set(existing["date"]) if not existing.empty else set()

    # 実測を始めた日より前だけを推計で埋める。
    # 実測が始まったあとに推計を混ぜると、記録した日と計算し直した日が交互に並び、
    # 1本の線の中で意味が入れ替わってしまう。境目は1か所だけにする。
    if not existing.empty:
        first_real = existing["date"].min()
        n_before = len(new)
        new = new[new["date"] < first_real]
        print(f"  実測の開始日: {first_real} — それ以降の推計 {n_before - len(new)} 行は使いません")

    add = new[~new["date"].isin(have)]
    if add.empty:
        print("  追加するものはありませんでした。")
        return

    print(f"  追加する: {len(add)} 行（{add['date'].min()}〜{add['date'].max()}）")
    print(add.head(3).to_string(index=False))
    print("  ...")
    print(add.tail(3).to_string(index=False))

    if args.dry_run:
        print("\n--dry-run なので書き込みませんでした。")
        return

    n = upsert_df("equity_history", add, _COLS)
    print(f"\n書き込みました: {n} 行")

    # 元からあった行に印が無い状態を残さない。印が無いと画面側で
    # 推計と実測の区別が付かなくなる。
    read_df("SELECT 1")   # 接続の生成をここで済ませる
    from modules.store import connect
    with connect() as conn:
        conn.execute("UPDATE equity_history SET source='snapshot' WHERE source IS NULL")
    after = read_df("SELECT source, COUNT(*) AS n, MIN(date) AS 開始, MAX(date) AS 終了 "
                    "FROM equity_history GROUP BY source")
    print(after.to_string(index=False))


if __name__ == "__main__":
    main()
