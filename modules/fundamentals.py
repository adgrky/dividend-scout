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

import math

import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ProgressFn = Callable[[float, str], None]


class RateLimited(RuntimeError):
    """yfinance の IP 単位のレート制限に当たった。

    この状態になると財務が一律に空で返る。実測では、1時間前に取れていた 9432 でさえ
    空になった。これを「財務が無い銘柄」として DB に記録すると、候補の半分が
    理由も分からず画面から消える（実測: 1,217 銘柄中 521 銘柄がこの状態だった）。
    握りつぶさず中断して、時間をおいてから --missing-only で取り直す。
    中断までに取れたぶんは partial_fund / partial_snap に載せて渡す（捨てない）。
    """

    def __init__(self, message: str, partial_fund: pd.DataFrame | None = None,
                 partial_snap: pd.DataFrame | None = None) -> None:
        super().__init__(message)
        self.partial_fund = partial_fund
        self.partial_snap = partial_snap


class _Throttle:
    """全スレッド共通の間隔制限。並列度を上げても呼び出し間隔を保つ。"""

    def __init__(self, min_interval: float) -> None:
        self._lock = threading.Lock()
        self._min = min_interval
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self._min - (now - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()

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


def _num(v) -> float | None:
    """yfinance の info から数字だけを取り出す。

    赤字の会社の PER に **文字列 "Infinity"** が返ってくる（実測: 2676.T / 3543.T）。
    JSON の Infinity トークンがそのまま文字列で渡ってくるためで、数値ではない。
    そのまま DB に入れると列全体が文字列になり、次のスキャンで
    `df["per"] > 0` が TypeError で落ちる。**画面には何も出ないまま更新が止まる。**
    取り込む手前で数字以外を落とす。
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None



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


def fetch_one(ticker: str, retries: int = 2,
              throttle: "_Throttle | None" = None) -> tuple[pd.DataFrame, dict]:
    """1銘柄の財務5期分＋最新スナップショットを取る。

    yfinance は並列度を上げるとレート制限（HTTP 429 / Invalid Crumb）で
    空の DataFrame を黙って返す。実測で 1,217 銘柄中 559 銘柄の財務が
    取れておらず、候補の半分近くが理由も分からず消えていた。
    空が返ったら待って取り直す。
    """
    fin = bs = cf = pd.DataFrame()
    for attempt in range(retries + 1):
        if throttle:
            throttle.wait()
        tk = yf.Ticker(ticker)
        try:
            fin, bs, cf = tk.financials, tk.balance_sheet, tk.cashflow
        except Exception:
            fin = bs = cf = pd.DataFrame()
        if fin is not None and not fin.empty:
            break
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
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
        "market_cap": _num(info.get("marketCap")),
        "per": _num(info.get("trailingPE")),
        "pbr": _num(info.get("priceToBook")),
        "roe": _num(info.get("returnOnEquity")),
        # info["payoutRatio"] は壊れていることがあるので参考値として持つだけ。
        # 実際の判定には modules/scoring.py で自前計算した値を使う。
        "payout_ratio": _num(info.get("payoutRatio")),
        "held_pct_institutions": _num(info.get("heldPercentInstitutions")),
    }
    return pd.DataFrame(rows).reindex(columns=FUNDAMENTAL_COLS), snap


def fetch_many(tickers: Iterable[str], workers: int = 3,
               progress: ProgressFn | None = None,
               min_interval: float = 0.4,
               empty_streak_limit: int = 25,
               strict: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """財務をまとめて取る。

    レート制限に当たると財務が一律に空で返るため、空が連続したら
    RateLimited を投げて中断する（strict=False なら警告だけ出して続ける）。
    途中まで取れたぶんは戻り値に含まれるので、呼び出し側で保存してよい。
    """
    tickers = list(tickers)
    fund_parts, snaps, failed = [], [], []
    done = 0
    empty_streak = 0
    limited = False
    throttle = _Throttle(min_interval)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_one, t, 2, throttle): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            done += 1
            try:
                df, snap = fut.result()
                if df.empty:
                    empty_streak += 1
                else:
                    empty_streak = 0
                    fund_parts.append(df)
                snaps.append(snap)
            except Exception:
                failed.append(t)
            if empty_streak >= empty_streak_limit and not limited:
                limited = True
                for f in futures:
                    f.cancel()
                break
            if progress and done % 20 == 0:
                progress(done / len(tickers), f"財務取得 {done}/{len(tickers)}")

    n_with_fin = len(fund_parts)
    fund = pd.concat(fund_parts, ignore_index=True) if fund_parts else pd.DataFrame(columns=FUNDAMENTAL_COLS)
    snap_df = pd.DataFrame(snaps).reindex(columns=SNAPSHOT_COLS)

    if limited:
        msg = (f"yfinance のレート制限に当たった（財務が {empty_streak} 銘柄連続で空）。"
               f"{done}/{len(tickers)} 銘柄まで処理、うち財務あり {n_with_fin}。"
               f"30分ほど空けてから --missing-only で取り直すこと。")
        if strict:
            raise RateLimited(msg, fund, snap_df)
        print(f"⚠️  {msg}")
    elif progress:
        progress(1.0, f"財務取得 完了（財務あり {n_with_fin} / 例外 {len(failed)} 件）")
    return fund, snap_df


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
