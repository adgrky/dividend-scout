"""権利確定日・会社予想配当・英文の事業概要を yfinance から取る。

EDINET から取れないのはこの3つだけ。
権利確定日は「いま買って次の配当に間に合うか」を判断するのに要る。

使い方:
    uv run python scripts/fetch_profiles.py
    uv run python scripts/fetch_profiles.py --limit 50
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from modules.config import load_config                          # noqa: E402
from modules.fundamentals import _Throttle                      # noqa: E402
from modules.pipeline import load_base, prescreen               # noqa: E402
from modules.store import connect, init_db, read_df             # noqa: E402

_COLS = ["ticker", "business_en", "ex_dividend_date", "dividend_rate", "industry_en", "updated_at"]


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def fetch_one(ticker: str, throttle) -> dict | None:
    import yfinance as yf
    throttle.wait()
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    if not info:
        return None
    ex = info.get("exDividendDate")
    ex_date = None
    if ex:
        try:
            ex_date = datetime.fromtimestamp(int(ex), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            ex_date = None
    return {
        "ticker": ticker,
        "business_en": (info.get("longBusinessSummary") or "")[:800] or None,
        "ex_dividend_date": ex_date,
        "dividend_rate": info.get("dividendRate"),
        "industry_en": info.get("industry"),
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--missing-only", action="store_true",
                    help="権利確定日がまだ入っていない銘柄だけ")
    args = ap.parse_args()

    init_db()
    targets = list(prescreen(load_base(), load_config()))
    if args.missing_only:
        have = set(read_df("SELECT ticker FROM company_profile "
                           "WHERE ex_dividend_date IS NOT NULL").ticker)
        targets = [t for t in targets if t not in have]
    if args.limit:
        targets = targets[:args.limit]

    _log(f"{len(targets)} 銘柄の権利確定日・会社予想配当を取得します…")
    throttle = _Throttle(0.4)
    rows, empty_streak = [], 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch_one, t, throttle): t for t in targets}
        for i, fut in enumerate(as_completed(futs), start=1):
            r = fut.result()
            if r is None:
                empty_streak += 1
            else:
                empty_streak = 0
                rows.append(r)
            if empty_streak >= 25:
                _log("⚠️ 連続で取得に失敗しました。レート制限の可能性があるので中断します。")
                break
            if i % 50 == 0:
                _log(f"  {i}/{len(targets)}（取得 {len(rows)}）")

    if not rows:
        _log("取得できたものがありません")
        return

    df = pd.DataFrame(rows)
    # EDINET が入れた business_ja / policy を消さないよう、列を指定して更新する
    with connect() as conn:
        for r in df.itertuples(index=False):
            conn.execute("""
                INSERT INTO company_profile (ticker, business_en, ex_dividend_date,
                                             dividend_rate, industry_en, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    business_en=excluded.business_en,
                    ex_dividend_date=excluded.ex_dividend_date,
                    dividend_rate=excluded.dividend_rate,
                    industry_en=excluded.industry_en,
                    updated_at=excluded.updated_at
            """, (r.ticker, r.business_en, r.ex_dividend_date, r.dividend_rate,
                  r.industry_en, r.updated_at))
    _log(f"完了: {len(df)} 銘柄 / {(time.time() - t0) / 60:.1f} 分")
    n = read_df("SELECT COUNT(*) n FROM company_profile WHERE ex_dividend_date IS NOT NULL").n[0]
    _log(f"権利確定日が入っている銘柄: {int(n)}")


if __name__ == "__main__":
    main()
