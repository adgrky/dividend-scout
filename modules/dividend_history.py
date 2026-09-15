"""配当履歴から「増配の実績」を測る。

このアプリで最も信頼できるデータがここ。yfinance の配当履歴は 2000年前後まで
遡れて（NTT で53件・26年分）、しかも株式分割調整済みで返る。財務諸表は5期分しか
取れないので、Phase 3 で EDINET を入れるまでは、増配実績の判定はこのモジュールが
一手に引き受ける。

【年度の切り方】
日本企業は中間（9月）と期末（3月）に分けて払うため、暦年で合算すると
「FY2025 = 2025年9月の中間 + 2026年3月の期末」がバラバラの年に散る。
決算期を推定する代わりに、**直近の権利落ち日を起点に1年ずつ遡ったバケット**で
合算する（pd.DateOffset(years=n) を使うので日付のズレが蓄積しない）。
年1回・年2回・年4回のどれでも同じロジックで正しく揃う。
このバケットは厳密には実質TTM（直近12ヶ月）であり、会計年度とは一致しない。
正確な会計年度ベースの DPS は Phase 3 の EDINET で入れる。

【記念配当・特別配当】
yfinance は普通配当と記念配当を区別しない。区別せずに扱うと、記念配当の翌年に
必ず「減配」が立ち、増配銘柄を誤って落としてしまう。ここでは
「一時的に跳ねて翌年に戻った年」をスパイクとして検出し、連続増配・減配回数の
判定からは除外する（値は消さず、フラグとして残す）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

# スパイク判定：前年比でこの倍率を超え、かつ翌年に前年並みへ戻ったら記念配当を疑う
_SPIKE_UP = 1.40
_SPIKE_REVERT = 1.15   # 翌年が「スパイク前 × この倍率」以下なら戻ったとみなす
# 減配とみなす下落幅。1円未満の端数調整で減配判定が立たないようにする
_CUT_TOLERANCE = 0.995


def annual_dps(div: pd.DataFrame) -> pd.DataFrame:
    """1銘柄の配当明細 -> 年度別 DPS。

    Parameters
    ----------
    div : DataFrame
        columns: date（YYYY-MM-DD 文字列 or datetime）, amount

    Returns
    -------
    DataFrame
        index: 年度ラベル（バケット終端の暦年）, columns: dps, n_payments, period_end
    """
    if div is None or div.empty:
        return pd.DataFrame(columns=["dps", "n_payments", "period_end"])
    d = div.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["amount"] > 0].sort_values("date")
    if d.empty:
        return pd.DataFrame(columns=["dps", "n_payments", "period_end"])

    anchor = d["date"].max()
    first = d["date"].min()
    n_years = max(1, int(math.ceil((anchor - first).days / 365.25)) + 1)

    # バケット n = (anchor - (n+1)年, anchor - n年]
    edges = [anchor - pd.DateOffset(years=n) for n in range(n_years + 1)]
    rows = []
    for n in range(n_years):
        hi, lo = edges[n], edges[n + 1]
        sel = d[(d["date"] > lo) & (d["date"] <= hi)]
        if sel.empty:
            continue
        rows.append({
            "year": int(hi.year),
            "dps": float(sel["amount"].sum()),
            "n_payments": int(len(sel)),
            "period_end": hi.strftime("%Y-%m-%d"),
        })
    if not rows:
        return pd.DataFrame(columns=["dps", "n_payments", "period_end"])
    out = pd.DataFrame(rows).set_index("year").sort_index()
    return out


def flag_spikes(dps: pd.Series) -> pd.Series:
    """記念配当・特別配当と思われる年に True を立てる。"""
    flags = pd.Series(False, index=dps.index)
    vals = dps.values
    for i in range(1, len(vals) - 1):
        prev, cur, nxt = vals[i - 1], vals[i], vals[i + 1]
        if prev <= 0:
            continue
        if cur >= prev * _SPIKE_UP and nxt <= prev * _SPIKE_REVERT:
            flags.iloc[i] = True
    return flags


@dataclass
class DividendProfile:
    """1銘柄の配当プロフィール。スコア計算はこれを入力に取る。"""
    ticker: str
    years_paying: int = 0
    dps_latest: float = 0.0
    dps_prev: float = 0.0
    streak: int = 0                 # 連続増配年数（据え置きは途切れないが伸びない）
    streak_no_cut: int = 0          # 減配なしで継続している年数
    cuts_10y: int = 0
    cuts_all: int = 0
    cagr_5y: float | None = None
    cagr_10y: float | None = None
    growth_latest: float | None = None
    payments_per_year: int = 0
    has_spike: bool = False
    series: dict = field(default_factory=dict)   # {年: DPS}

    def to_dict(self) -> dict:
        return asdict(self)


def _cagr(series: pd.Series, years: int) -> float | None:
    """n年の年率成長率。始点が0または欠損なら None。"""
    if len(series) < years + 1:
        return None
    end = series.iloc[-1]
    start = series.iloc[-(years + 1)]
    if start <= 0 or end <= 0:
        return None
    return float((end / start) ** (1 / years) - 1)


def build_profile(ticker: str, div: pd.DataFrame) -> DividendProfile:
    table = annual_dps(div)
    if table.empty:
        return DividendProfile(ticker=ticker)

    dps = table["dps"]
    spikes = flag_spikes(dps)

    # 連続増配・減配回数はスパイク（記念配当）の年を挟んでも途切れないよう、
    # スパイク年を取り除いた系列で判定する
    clean = dps[~spikes]

    streak = 0
    streak_no_cut = 0
    vals = clean.values
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] > vals[i - 1]:
            streak += 1
        else:
            break
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] >= vals[i - 1] * _CUT_TOLERANCE:
            streak_no_cut += 1
        else:
            break

    cuts_all = int((clean.diff() < 0).sum()) if len(clean) > 1 else 0
    recent = clean[clean.index >= clean.index.max() - 10]
    cuts_10y = int((recent.diff() < 0).sum()) if len(recent) > 1 else 0

    growth = None
    if len(dps) >= 2 and dps.iloc[-2] > 0:
        growth = float(dps.iloc[-1] / dps.iloc[-2] - 1)

    return DividendProfile(
        ticker=ticker,
        years_paying=int(len(dps)),
        dps_latest=float(dps.iloc[-1]),
        dps_prev=float(dps.iloc[-2]) if len(dps) >= 2 else 0.0,
        streak=streak,
        streak_no_cut=streak_no_cut,
        cuts_10y=cuts_10y,
        cuts_all=cuts_all,
        cagr_5y=_cagr(dps, 5),
        cagr_10y=_cagr(dps, 10),
        growth_latest=growth,
        payments_per_year=int(table["n_payments"].iloc[-1]),
        has_spike=bool(spikes.any()),
        series={int(k): float(v) for k, v in dps.items()},
    )


def build_profiles(dividends: pd.DataFrame) -> dict[str, DividendProfile]:
    """全銘柄分をまとめて作る。dividends は ticker/date/amount の縦持ち。"""
    out: dict[str, DividendProfile] = {}
    if dividends is None or dividends.empty:
        return out
    for ticker, grp in dividends.groupby("ticker", sort=False):
        out[str(ticker)] = build_profile(str(ticker), grp)
    return out


def profiles_to_frame(profiles: dict[str, DividendProfile]) -> pd.DataFrame:
    if not profiles:
        return pd.DataFrame()
    rows = []
    for p in profiles.values():
        d = p.to_dict()
        d.pop("series", None)
        rows.append(d)
    return pd.DataFrame(rows).set_index("ticker")
