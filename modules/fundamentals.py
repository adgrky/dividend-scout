"""財務データの取得（Stage 2）。

yfinance の財務諸表は **4〜5期分しか返らない**（実測: 9432 で4期、4967 で5期）。
EPS の10年推移は取れないので、増配の持続性を財務面から本当に判定するには
Phase 3 の EDINET XBRL が必要。ここで取れるのはあくまで「直近の体力」であり、
長期の増配実績は modules/dividend_history.py 側が担う。

個別に yf.Ticker を叩くので1銘柄あたり数秒かかる。必ずゲートを通した数百銘柄だけに
絞ってから呼ぶこと。全市場3,700銘柄に投げると数時間かかる。

info["payoutRatio"] は信用しない（実測: 4967 で 4.80 = 480%）。
配当性向は DPS × 発行済株数 ÷ 純利益 で自前計算する。
"""
from __future__ import annotations

import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ProgressFn = Callable[[float, str], None]

FUNDAMENTAL_COLS = ["ticker", "fiscal_end", "net_income", "revenue", "operating_income",
                    "operating_cf", "free_cf", "total_equity", "total_assets",
                    "total_debt", "cash", "shares"]
SNAPSHOT_COLS = ["ticker", "asof", "market_cap", "per", "pbr", "roe",
                 "payout_ratio", "held_pct_institutions"]

# yfinance の行ラベルは版によって揺れるので候補を順に探す
_ROW_ALIASES = {
    "net_income": ["Net Income", "Net Income Common Stockholders",
                   "Net Income From Continuing Operation Net Minority Interest"],
    "revenue": ["Total Revenue", "Operating Revenue"],
    "operating_income": ["Operating Income", "EBIT"],
    "operating_cf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "free_cf": ["Free Cash Flow"],
    "total_equity": ["Stockholders Equity", "Total Equity Gross Minority Interest",
                     "Common Stock Equity"],
    "total_assets": ["Total Assets"],
    "total_debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments",
             "Cash And Cash Equivalents"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
}


def _pick(frames: list[pd.DataFrame], key: str, col) -> float | None:
    for name in _ROW_ALIASES[key]:
        for df in frames:
            if df is None or df.empty or name not in df.index or col not in df.columns:
                continue
            val = df.loc[name, col]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            if pd.notna(val):
                return float(val)
    return None


def fetch_one(ticker: str) -> tuple[pd.DataFrame, dict]:
    """1銘柄の財務5期分＋最新スナップショットを取る。"""
    tk = yf.Ticker(ticker)
    try:
        fin, bs, cf = tk.financials, tk.balance_sheet, tk.cashflow
    except Exception:
        fin = bs = cf = pd.DataFrame()
    frames = [f for f in (fin, bs, cf) if f is not None and not f.empty]

    rows = []
    cols = sorted({c for f in frames for c in f.columns}, reverse=True)
    for col in cols:
        rec = {"ticker": ticker, "fiscal_end": pd.Timestamp(col).strftime("%Y-%m-%d")}
        for key in _ROW_ALIASES:
            rec[key] = _pick(frames, key, col)
        if any(rec[k] is not None for k in _ROW_ALIASES):
            rows.append(rec)

    try:
        info = tk.info or {}
    except Exception:
        info = {}
    snap = {
        "ticker": ticker,
        "asof": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "market_cap": info.get("marketCap"),
        "per": info.get("trailingPE"),
        "pbr": info.get("priceToBook"),
        "roe": info.get("returnOnEquity"),
        # info["payoutRatio"] は壊れていることがあるので参考値として持つだけ。
        # 実際の判定には modules/scoring.py で自前計算した値を使う。
        "payout_ratio": info.get("payoutRatio"),
        "held_pct_institutions": info.get("heldPercentInstitutions"),
    }
    return pd.DataFrame(rows).reindex(columns=FUNDAMENTAL_COLS), snap


def fetch_many(tickers: Iterable[str], workers: int = 8,
               progress: ProgressFn | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    tickers = list(tickers)
    fund_parts, snaps, failed = [], [], []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            done += 1
            try:
                df, snap = fut.result()
                if not df.empty:
                    fund_parts.append(df)
                snaps.append(snap)
            except Exception:
                failed.append(t)
            if progress and done % 20 == 0:
                progress(done / len(tickers), f"財務取得 {done}/{len(tickers)}")
    if progress:
        progress(1.0, f"財務取得 完了（失敗 {len(failed)} 件）")
    fund = pd.concat(fund_parts, ignore_index=True) if fund_parts else pd.DataFrame(columns=FUNDAMENTAL_COLS)
    return fund, pd.DataFrame(snaps).reindex(columns=SNAPSHOT_COLS)


def latest_metrics(fund: pd.DataFrame) -> pd.DataFrame:
    """銘柄ごとに直近期の値と、5期分から導く安定性指標を作る。"""
    if fund is None or fund.empty:
        return pd.DataFrame()
    f = fund.sort_values(["ticker", "fiscal_end"])
    rows = []
    for ticker, g in f.groupby("ticker", sort=False):
        last = g.iloc[-1]
        ni = g["net_income"]
        ocf = g["operating_cf"]
        shares_s = g["shares"].dropna()
        equity = last.get("total_equity")
        assets = last.get("total_assets")
        debt = last.get("total_debt") or 0.0
        cash = last.get("cash") or 0.0
        rows.append({
            "ticker": ticker,
            "n_periods": len(g),
            "fiscal_end": last["fiscal_end"],
            "net_income": last.get("net_income"),
            "revenue": last.get("revenue"),
            "operating_income": last.get("operating_income"),
            "operating_cf": last.get("operating_cf"),
            "free_cf": last.get("free_cf"),
            "total_equity": equity,
            "total_assets": assets,
            "total_debt": debt,
            "cash": cash,
            "shares": last.get("shares"),
            "equity_ratio": (equity / assets) if equity and assets else None,
            "net_cash": (cash - debt) if (cash is not None and debt is not None) else None,
            "net_income_prev": float(ni.iloc[-2]) if len(ni) >= 2 and pd.notna(ni.iloc[-2]) else None,
            "loss_years": int((ni < 0).sum()),
            "positive_ocf_years": int((ocf > 0).sum()),
            "ni_cagr": _cagr(ni),
            "ocf_cagr": _cagr(ocf),
            "share_growth": _cagr(shares_s),
            "ni_declining_years": _declining_streak(ni),
        })
    return pd.DataFrame(rows).set_index("ticker")


def _declining_streak(s: pd.Series) -> int:
    """直近から遡って純利益が連続で減った期数。"""
    v = s.dropna().values
    n = 0
    for i in range(len(v) - 1, 0, -1):
        if v[i] < v[i - 1]:
            n += 1
        else:
            break
    return n


def _cagr(s: pd.Series) -> float | None:
    s = s.dropna()
    if len(s) < 2:
        return None
    start, end, n = s.iloc[0], s.iloc[-1], len(s) - 1
    if start <= 0 or end <= 0:
        return None
    return float((end / start) ** (1 / n) - 1)
