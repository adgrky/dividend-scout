"""割安さを「その銘柄自身の過去」と比べて測る。

絶対利回り（5%だから割安）は業種特性と市場全体の水準に引きずられて役に立たない。
ヘムのやり方に近いのは「その銘柄がふだん何%で取引されているか」を知った上で、
いまその分布のどこにいるかを見ること。これを自己ヒストリカル利回りパーセンタイルと
呼んで、E層の中心指標に据える。

週足の終値と、その時点までに判明していた TTM DPS から利回り系列を作る。
将来の増配を過去の時点に持ち込まないよう、各週の DPS は必ず「その週までの
直近12ヶ月に権利落ちした配当の合計」だけを使う（先読み防止）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ttm_dps_series(prices: pd.DataFrame, dividends: pd.DataFrame) -> pd.Series:
    """週足の各時点における TTM DPS（先読みなし）。

    Parameters
    ----------
    prices : DataFrame  columns: date, close（1銘柄分・昇順）
    dividends : DataFrame  columns: date, amount（1銘柄分）
    """
    if prices is None or prices.empty:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(prices["date"])
    if dividends is None or dividends.empty:
        return pd.Series(0.0, index=idx)

    div = dividends.copy()
    div["date"] = pd.to_datetime(div["date"])
    div = div[div["amount"] > 0].sort_values("date")
    if div.empty:
        return pd.Series(0.0, index=idx)

    # 各週末について、直近12ヶ月の配当合計を累積和の差で取る
    cum = div.set_index("date")["amount"].cumsum()
    cum_at = cum.reindex(cum.index.union(idx)).ffill().reindex(idx).fillna(0.0)
    year_ago = idx - pd.DateOffset(years=1)
    cum_prev = cum.reindex(cum.index.union(pd.DatetimeIndex(year_ago))).ffill()
    cum_prev = cum_prev.reindex(pd.DatetimeIndex(year_ago)).fillna(0.0)
    ttm = pd.Series(cum_at.values - cum_prev.values, index=idx).clip(lower=0.0)
    return ttm


def yield_series(prices: pd.DataFrame, dividends: pd.DataFrame) -> pd.Series:
    if prices is None or prices.empty:
        return pd.Series(dtype=float)
    close = pd.Series(prices["close"].values, index=pd.to_datetime(prices["date"]))
    ttm = ttm_dps_series(prices, dividends)
    y = (ttm / close).replace([np.inf, -np.inf], np.nan)
    return y.dropna()


def yield_percentile(prices: pd.DataFrame, dividends: pd.DataFrame,
                     window_years: int = 7) -> dict:
    """現在の利回りが、自分の過去N年の分布のどこにいるか。

    Returns
    -------
    dict
        current      現在の TTM 利回り
        percentile   0〜1。1 に近いほど「自分史上まれに見る高利回り＝割安」
        median / p25 / p75   過去分布
        target_price_at_median  過去中央値の利回りまで戻った場合の株価
    """
    y = yield_series(prices, dividends)
    if y.empty:
        return {}
    cutoff = y.index.max() - pd.DateOffset(years=window_years)
    hist = y[y.index >= cutoff]
    # 無配期間（0）は分布を歪めるので除く
    hist = hist[hist > 0]
    if len(hist) < 52:  # 1年分未満は判定しない
        return {"current": float(y.iloc[-1]) if y.iloc[-1] > 0 else None}
    cur = float(y.iloc[-1])
    if cur <= 0:
        return {"current": None}
    med = float(hist.median())
    close_now = float(prices["close"].iloc[-1])
    return {
        "current": cur,
        "percentile": float((hist < cur).mean()),
        "median": med,
        "p25": float(hist.quantile(0.25)),
        "p75": float(hist.quantile(0.75)),
        "n_obs": int(len(hist)),
        "price_at_median_yield": close_now * cur / med if med > 0 else None,
    }


def price_for_target_yield(dps: float, target_yield: float) -> float | None:
    """目標利回りに届く株価＝指値。"""
    if not dps or not target_yield or target_yield <= 0:
        return None
    return float(dps / target_yield)


def build_valuation_table(prices: pd.DataFrame, dividends: pd.DataFrame,
                          window_years: int = 7) -> pd.DataFrame:
    """全銘柄分をまとめて。prices/dividends は ticker 付きの縦持ち。"""
    rows = []
    div_by = {t: g for t, g in dividends.groupby("ticker", sort=False)} if not dividends.empty else {}
    for ticker, g in prices.groupby("ticker", sort=False):
        g = g.sort_values("date")
        info = yield_percentile(g, div_by.get(ticker, pd.DataFrame(columns=["date", "amount"])),
                                window_years)
        if info:
            info["ticker"] = ticker
            rows.append(info)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("ticker")
