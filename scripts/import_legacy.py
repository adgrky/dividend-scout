"""portfolio-manager から保有・ウォッチリスト・売買記録を取り込む。

配当履歴（dividends_history.csv）と extended_meta.csv は **取り込まない**。
どちらも同じ yfinance 由来だが、こちらは毎回 period="max" で取り直すため
履歴が長く（2000年〜）、株式分割調整も最新の状態で揃う。古い CSV を混ぜると
分割前後の DPS が混在して増配判定が壊れるので、株価・配当は常に取り直す。

使い方:
    uv run python scripts/import_legacy.py
    uv run python scripts/import_legacy.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.config import legacy_dir, load_config          # noqa: E402
from modules.store import init_db, read_df, upsert_df        # noqa: E402
from modules.universe import to_ticker                       # noqa: E402

_HOLDING_COLS = ["account", "ticker", "name", "shares", "avg_cost",
                 "target_yield", "bottom_yield", "updated_at"]
_WATCH_COLS = ["ticker", "name", "target_yield", "bottom_yield",
               "source", "thesis", "invalidation", "added_at", "note"]
_TX_COLS = ["date", "account", "ticker", "name", "type", "shares", "price", "fee", "memo"]


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"  スキップ（無し）: {path.name}")
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"ticker": str})
    print(f"  読み込み: {path.name} ({len(df)} 行)")
    return df


def build_holdings(src: Path) -> pd.DataFrame:
    parts = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for account, fname in (("specific", "specific.csv"), ("nisa", "nisa.csv")):
        df = _load_csv(src / fname)
        if df.empty:
            continue
        df = df[df["ticker"].notna()].copy()
        df["account"] = account
        df["ticker"] = df["ticker"].map(to_ticker)
        df["updated_at"] = now
        parts.append(df.reindex(columns=_HOLDING_COLS))
    if not parts:
        return pd.DataFrame(columns=_HOLDING_COLS)
    out = pd.concat(parts, ignore_index=True)
    # 同一口座で同じ銘柄が重複していたら株数を合算し、取得単価は加重平均にする
    out["_cost"] = out["shares"] * out["avg_cost"].fillna(0)
    agg = out.groupby(["account", "ticker"], as_index=False).agg(
        name=("name", "first"), shares=("shares", "sum"), _cost=("_cost", "sum"),
        target_yield=("target_yield", "first"), bottom_yield=("bottom_yield", "first"),
        updated_at=("updated_at", "first"),
    )
    agg["avg_cost"] = (agg["_cost"] / agg["shares"]).where(agg["shares"] > 0)
    return agg.reindex(columns=_HOLDING_COLS)


def build_watchlist(src: Path) -> pd.DataFrame:
    df = _load_csv(src / "watchlist.csv")
    if df.empty:
        return pd.DataFrame(columns=_WATCH_COLS)
    df = df[df["ticker"].notna()].copy()
    df["ticker"] = df["ticker"].map(to_ticker)
    df["source"] = "manual"
    df["added_at"] = datetime.now().strftime("%Y-%m-%d")
    df["thesis"] = None
    df["invalidation"] = None
    return df.drop_duplicates(subset=["ticker"]).reindex(columns=_WATCH_COLS)


def build_transactions(src: Path) -> pd.DataFrame:
    df = _load_csv(src / "transactions.csv")
    if df.empty:
        return pd.DataFrame(columns=_TX_COLS)
    df = df[df["ticker"].notna()].copy()
    df["ticker"] = df["ticker"].map(to_ticker)
    return df.reindex(columns=_TX_COLS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB に書かず件数だけ出す")
    ap.add_argument("--src", default=None, help="portfolio-manager の data ディレクトリ")
    args = ap.parse_args()

    config = load_config()
    src = Path(args.src).resolve() if args.src else legacy_dir(config)
    print(f"取り込み元: {src}")
    if not src.exists():
        print("❌ 取り込み元が見つからない")
        raise SystemExit(1)

    holdings = build_holdings(src)
    watch = build_watchlist(src)
    tx = build_transactions(src)

    print()
    print(f"保有:         {len(holdings)} 件 "
          f"(特定 {int((holdings['account'] == 'specific').sum())} / "
          f"NISA {int((holdings['account'] == 'nisa').sum())})")
    print(f"ウォッチリスト: {len(watch)} 件")
    print(f"売買記録:      {len(tx)} 件")

    if args.dry_run:
        print("\n--dry-run のため書き込みなし")
        return

    init_db()
    upsert_df("holdings", holdings, _HOLDING_COLS)
    upsert_df("watchlist", watch, _WATCH_COLS)
    if not tx.empty:
        upsert_df("transactions", tx, _TX_COLS)

    n_h = read_df("SELECT COUNT(*) n FROM holdings").n.iloc[0]
    n_w = read_df("SELECT COUNT(*) n FROM watchlist").n.iloc[0]
    print(f"\n✅ DB 反映: holdings={n_h} / watchlist={n_w}")
    print("※ 配当履歴と財務は weekly_scan.py で取り直す（分割調整済みの最新を使うため）")


if __name__ == "__main__":
    main()
