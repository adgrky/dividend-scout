"""データ品質のチェック。

yfinance には壊れた銘柄が混ざる。実測した例:
    8303.T（旧新生銀行）… 2023年に比率 5e-08 の「分割」が記録されており、
    週足終値が 553億円 に跳ねていた。この1銘柄のせいで検証のトータルリターン
    平均が +480,000% になり、指標の良し悪しがまったく読めなくなった。

順位相関は外れ値に強いので影響を受けないが、平均・分位の集計は壊れる。
取り込みの時点で弾き、集計の時点でも念のため落とす。
"""
from __future__ import annotations

import pandas as pd

# 株価が1週間で何倍まで動きうるか。分割調整済みの終値なので、
# これを超える段差はデータ破損とみなす（ストップ高連続でも20倍は動かない）。
MAX_WEEKLY_JUMP = 20.0
# 分割比率として妥当な範囲。1/1000（1000株併合）〜1000倍分割まで。
MIN_SPLIT_RATIO = 1e-3
MAX_SPLIT_RATIO = 1e3


def price_series_is_sane(close: pd.Series) -> tuple[bool, str]:
    """終値の系列が壊れていないか。"""
    c = close.dropna()
    if len(c) < 2:
        return True, ""
    if (c <= 0).any():
        return False, "終値に0以下が含まれる"
    ratio = (c / c.shift(1)).dropna()
    worst_up = ratio.max()
    worst_dn = ratio.min()
    if worst_up > MAX_WEEKLY_JUMP:
        idx = ratio.idxmax()
        return False, f"終値が1期間で {worst_up:,.0f} 倍に跳ねている（{idx:%Y-%m-%d}）"
    if worst_dn < 1 / MAX_WEEKLY_JUMP:
        idx = ratio.idxmin()
        return False, f"終値が1期間で 1/{1 / worst_dn:,.0f} に落ちている（{idx:%Y-%m-%d}）"
    return True, ""


def split_is_sane(ratio: float) -> bool:
    return MIN_SPLIT_RATIO <= float(ratio) <= MAX_SPLIT_RATIO


def find_broken_tickers(prices: pd.DataFrame) -> pd.DataFrame:
    """縦持ちの価格テーブルから壊れた銘柄を洗い出す。"""
    rows = []
    for ticker, g in prices.groupby("ticker", sort=False):
        s = pd.Series(g["close"].values, index=pd.to_datetime(g["date"])).sort_index()
        ok, reason = price_series_is_sane(s)
        if not ok:
            rows.append({"ticker": ticker, "理由": reason})
    return pd.DataFrame(rows)


def winsorize(s: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """裾を刈って平均を使えるようにする。中央値を見るなら不要。"""
    v = s.dropna()
    if v.empty:
        return s
    lo, hi = v.quantile(lower), v.quantile(upper)
    return s.clip(lo, hi)


def last_bad_jump(close: pd.Series) -> pd.Timestamp | None:
    """最後に起きた異常な段差の日付。無ければ None。"""
    c = close.dropna()
    if len(c) < 2:
        return None
    ratio = (c / c.shift(1)).dropna()
    bad = ratio[(ratio > MAX_WEEKLY_JUMP) | (ratio < 1 / MAX_WEEKLY_JUMP)]
    return bad.index.max() if len(bad) else None


def trim_to_sane(close: pd.Series) -> pd.Series:
    """破損箇所より後だけ残す。

    銘柄ごと捨てない。実測した39銘柄のうち大半は2002〜2013年の古い分割が
    未調整なだけで、直近のデータは正しい。丸ごと捨てると使える履歴まで失う。
    """
    at = last_bad_jump(close)
    if at is None:
        return close
    return close[close.index > at]


def trim_frame(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """縦持ちの価格テーブルから破損区間を落とす。

    Returns
    -------
    (トリム後のテーブル, 何をどれだけ落としたかの一覧)
    """
    if prices is None or prices.empty:
        return prices, pd.DataFrame()
    keep, notes = [], []
    for ticker, g in prices.groupby("ticker", sort=False):
        g = g.copy()
        idx = pd.to_datetime(g["date"])
        s = pd.Series(g["close"].values, index=idx)
        at = last_bad_jump(s)
        if at is None:
            keep.append(g)
            continue
        mask = (idx > at).values
        notes.append({"ticker": ticker, "破損日": at.strftime("%Y-%m-%d"),
                      "落とした行数": int((~mask).sum()), "残した行数": int(mask.sum())})
        if mask.any():
            keep.append(g[mask])
    out = pd.concat(keep, ignore_index=True) if keep else prices.iloc[0:0]
    return out, pd.DataFrame(notes)
