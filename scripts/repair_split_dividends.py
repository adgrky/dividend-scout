"""すでに貯めてある配当のうち、分割が漏れているものを直す。

権利落ち日と分割日が同じ日の配当だけ、yfinance が分割を当てずに返してくる
（詳しくは modules/bulk_fetch.py の冒頭）。取り込み側は直したので、これから取る
ぶんは正しくなる。このスクリプトは**すでに入っている古いぶん**を直す。

全市場を取り直す（更新.command、約20分）と結果的に直るが、それを待たずに
配当性向・年間配当を正しくしたいときに使う。

使い方:
    uv run python scripts/repair_split_dividends.py --dry-run
    uv run python scripts/repair_split_dividends.py
    uv run python scripts/repair_split_dividends.py --held-only   # 保有銘柄だけ
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.bulk_fetch import fix_same_day_split_dividends   # noqa: E402
from modules.config import bridge_secrets_to_env              # noqa: E402
from modules.store import connect, init_db, read_df, upsert_df  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="書き込まずに内容だけ出す")
    ap.add_argument("--held-only", action="store_true", help="保有している銘柄だけ直す")
    args = ap.parse_args()

    bridge_secrets_to_env()
    init_db()

    div = read_df("SELECT ticker, date, amount FROM dividends")
    sp = read_df("SELECT ticker, date, ratio FROM splits")
    if div.empty or sp.empty:
        print("配当か分割のデータがありません。")
        return

    if args.held_only:
        held = set(read_df("SELECT DISTINCT ticker FROM holdings")["ticker"])
        div = div[div["ticker"].isin(held)]
        sp = sp[sp["ticker"].isin(held)]
        print(f"保有 {len(held)} 銘柄にしぼります")

    fixed, log = fix_same_day_split_dividends(div, sp)
    if log.empty:
        print("直すものはありませんでした。")
        return

    names = read_df("SELECT ticker, name FROM universe").set_index("ticker")["name"].to_dict()
    held = set(read_df("SELECT DISTINCT ticker FROM holdings")["ticker"])
    log["銘柄名"] = log["ticker"].map(names).fillna("")
    log["保有"] = log["ticker"].map(lambda t: "★" if t in held else "")

    print(f"\n直す配当: {len(log)} 件 / {log['ticker'].nunique()} 銘柄"
          f"（うち保有 {(log['保有'] == '★').sum()} 件）\n")
    show = log[log["保有"] == "★"] if (log["保有"] == "★").any() else log.head(20)
    print(show[["ticker", "銘柄名", "date", "ratio", "before", "after"]]
          .rename(columns={"ticker": "コード", "date": "権利落ち日", "ratio": "分割比",
                           "before": "いまの値", "after": "直した値"})
          .to_string(index=False))

    if args.dry_run:
        print("\n--dry-run なので書き込みませんでした。")
        return

    rows = fixed.merge(log[["ticker", "date"]], on=["ticker", "date"], how="inner")
    n = upsert_df("dividends", rows, ["ticker", "date", "amount"])
    print(f"\n書き込みました: {n} 件")
    print("スコアと配当性向は、次に全市場スキャン（更新.command）を回すと計算し直されます。")


if __name__ == "__main__":
    main()
