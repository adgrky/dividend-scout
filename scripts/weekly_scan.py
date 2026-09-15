"""全市場フルスキャン（ヘッドレス）。

Stage 0: JPX 公式一覧から全上場銘柄＋33業種区分を取る
Stage 1: yfinance 一括ダウンロードで株価（週足）・配当・分割・最新値を取る
         → ここまでが Phase 0。Phase 1 でこの後にゲート判定とスコア計算が入る

Streamlit に一切依存しないので、GitHub Actions からも同じコードで走る。

使い方:
    uv run python scripts/weekly_scan.py --limit 300   # 通しの動作確認
    uv run python scripts/weekly_scan.py               # 全市場
    uv run python scripts/weekly_scan.py --holdings-only
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.bulk_fetch import scan                                   # noqa: E402
from modules.config import load_config                                # noqa: E402
from modules.dividend_history import build_profiles, profiles_to_frame  # noqa: E402
from modules.store import connect, init_db, read_df, upsert_df        # noqa: E402
from modules.universe import fetch_universe                           # noqa: E402

_UNIVERSE_COLS = ["ticker", "code", "name", "sector33", "market", "scale", "updated_at"]
_QUOTE_COLS = ["ticker", "asof", "last_close", "high_52w", "low_52w", "pos_52w",
               "avg_turnover", "ret_1y", "listing_start", "n_bars"]


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def refresh_universe(config: dict) -> pd.DataFrame:
    _log("Stage 0: JPX から上場銘柄一覧を取得中...")
    uni = fetch_universe(config["universe"]["markets"])
    uni["updated_at"] = datetime.now().strftime("%Y-%m-%d")
    upsert_df("universe", uni, _UNIVERSE_COLS)
    _log(f"Stage 0: {len(uni)} 銘柄 / {uni['sector33'].nunique()} 業種")
    return uni


def refresh_prices(tickers: list[str], config: dict) -> dict:
    _log(f"Stage 1: {len(tickers)} 銘柄の株価・配当を一括取得中...")
    t0 = time.time()
    res = scan(tickers, config, progress=lambda p, m: _log(f"  {p:5.1%} {m}"))
    elapsed = time.time() - t0

    upsert_df("prices", res["prices"], ["ticker", "date", "close", "volume"])
    upsert_df("dividends", res["dividends"], ["ticker", "date", "amount"])
    upsert_df("splits", res["splits"], ["ticker", "date", "ratio"])
    upsert_df("quotes", res["quotes"], _QUOTE_COLS)

    _log(f"Stage 1: 完了 {elapsed:.0f}秒 / 週足 {len(res['prices']):,} 行 / "
         f"配当 {len(res['dividends']):,} 行 / 銘柄 {len(res['quotes']):,}")
    res["elapsed"] = elapsed
    return res


def summarize_dividends() -> pd.DataFrame:
    """DB の配当履歴から全銘柄の配当プロフィールを作って表示用に返す。"""
    div = read_df("SELECT ticker, date, amount FROM dividends")
    profiles = build_profiles(div)
    return profiles_to_frame(profiles)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="先頭 N 銘柄だけ処理する")
    ap.add_argument("--holdings-only", action="store_true",
                    help="保有＋ウォッチリストだけ更新する")
    ap.add_argument("--skip-universe", action="store_true",
                    help="JPX 取得を飛ばして DB のユニバースを使う")
    args = ap.parse_args()

    config = load_config()
    init_db()
    started = datetime.now()

    if args.skip_universe:
        uni = read_df("SELECT * FROM universe")
        _log(f"Stage 0: スキップ（DB の {len(uni)} 銘柄を使用）")
    else:
        uni = refresh_universe(config)

    if args.holdings_only:
        tickers = read_df(
            "SELECT ticker FROM holdings UNION SELECT ticker FROM watchlist"
        ).ticker.tolist()
        _log(f"対象: 保有＋ウォッチリスト {len(tickers)} 銘柄")
    else:
        tickers = uni["ticker"].tolist()
        if args.limit:
            tickers = tickers[:args.limit]

    res = refresh_prices(tickers, config)

    prof = summarize_dividends()
    n_payers = int((prof["years_paying"] >= 5).sum()) if not prof.empty else 0
    n_streak5 = int((prof["streak"] >= 5).sum()) if not prof.empty else 0
    n_nocut10 = int(((prof["cuts_10y"] == 0) & (prof["years_paying"] >= 10)).sum()) if not prof.empty else 0

    _log(f"配当プロフィール: 5年以上の配当実績 {n_payers} / "
         f"5年以上連続増配 {n_streak5} / 10年減配なし {n_nocut10}")

    with connect() as conn:
        conn.execute(
            "INSERT INTO scan_runs (started_at, finished_at, kind, n_universe, n_passed, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (started.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds"),
             "weekly", len(tickers), n_payers,
             f"prices={len(res['prices'])} dividends={len(res['dividends'])} "
             f"elapsed={res['elapsed']:.0f}s"),
        )
    _log("完了")


if __name__ == "__main__":
    main()
