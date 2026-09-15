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

from datetime import date

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


def payout_months(dividends: pd.DataFrame, years: int = 3) -> pd.Series:
    """銘柄ごとの「配当がある月」を出す。

    yfinance の exDividendDate は直近の1回しか返さないので、3月決算の会社は
    9月（中間）しか出ず、期末の3月が見えない（実測で9月が479銘柄に偏った）。
    受け取り月を平準化したいときに使うのだから、年間のパターンが要る。

    Returns
    -------
    Series  index=ticker, value=月の集合（例 {3, 9}）
    """
    if dividends is None or dividends.empty:
        return pd.Series(dtype=object)
    d = dividends.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["amount"] > 0]
    if d.empty:
        return pd.Series(dtype=object)
    cutoff = d["date"].max() - pd.DateOffset(years=years)
    d = d[d["date"] >= cutoff]
    return d.groupby("ticker")["date"].apply(lambda s: sorted(set(s.dt.month)))


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


def next_ex_dates(tickers: list[str] | None = None, lookback_years: int = 3,
                  horizon_days: int = 400) -> pd.DataFrame:
    """次の権利落ち日を、配当履歴から予測する。

    【なぜ予測するか】
    yfinance の ex_dividend_date は実測で **1,272社のうち5社しか入っていない**
    （保有86銘柄では0件）。そのままでは使えない。

    【どう置くか】
    日本株の権利落ち日は決算期末に固定されていて、実測では 2021〜2026年の
    25,085件のうち **23,643件（94.2%）が「その月の最終営業日の1営業日前」**。
    年をまたいでも比率は変わらない。

    そこで、その銘柄自身の過去の権利落ち日が「月の最終営業日の何営業日前か」を
    測り、同じ位置に置く。月末に張り付いていない銘柄（月の途中で落ちるもの）は
    同じ日付を使い、休業日なら手前の営業日にずらす。

    **月末を土日だけで計算してはいけない。** 祝日をまたぐ月でずれ、しかも
    ずれる方向が「実際より後ろ」になる。「その日までに買えば間に合う」と出した
    日にはもう権利が落ちていて、配当を1回取り逃がす。東証の休業日は
    modules/jp_calendar.py で持つ。

    **予測であって会社の発表ではない**ので、画面では必ずそう書くこと。
    """
    from modules.jp_calendar import (last_trading_day, prev_trading_day,
                                     shift_trading_days, trading_days_before_month_end)
    from modules.store import read_df
    if tickers:
        ph = ",".join("?" * len(tickers))
        div = read_df(f"SELECT ticker, date, amount FROM dividends WHERE ticker IN ({ph})",
                      tuple(tickers))
    else:
        div = read_df("SELECT ticker, date, amount FROM dividends")
    cols = ["ticker", "次の権利落ち日", "あと何日", "1株配当の目安", "根拠"]
    if div.empty:
        return pd.DataFrame(columns=cols)

    div["date"] = pd.to_datetime(div["date"])
    today = pd.Timestamp.today().normalize()
    recent = div[div["date"] >= today - pd.DateOffset(years=lookback_years)]
    if recent.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for ticker, g in recent.groupby("ticker"):
        g = g.sort_values("date")
        best = None
        for month, mg in g.groupby(g["date"].dt.month):
            last = mg.iloc[-1]
            hist = last["date"].date()
            # 月末からの位置（営業日単位）。0〜3営業日前なら「月末張り付き」とみなす
            offset = trading_days_before_month_end(hist)
            at_month_end = offset <= 3
            note = (f"前年の{int(month)}月は月末の{offset}営業日前"
                    if at_month_end else f"前年の{int(month)}月{hist.day}日")

            for add_year in (0, 1):
                year = today.year + add_year
                try:
                    if at_month_end:
                        cand = shift_trading_days(
                            last_trading_day(year, int(month)), -offset) if offset                             else last_trading_day(year, int(month))
                    else:
                        cand = date(year, int(month), hist.day)
                        while not _is_td(cand):
                            cand = prev_trading_day(cand)
                except ValueError:
                    continue
                ts = pd.Timestamp(cand)
                if ts <= today or (ts - today).days > horizon_days:
                    continue
                if best is None or ts < best[0]:
                    best = (ts, float(last["amount"]), note)
                break
        if best:
            rows.append({
                "ticker": ticker,
                "次の権利落ち日": best[0].date(),
                "あと何日": int((best[0] - today).days),
                "1株配当の目安": best[1],
                "根拠": best[2],
            })
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).sort_values("次の権利落ち日").reset_index(drop=True)


def _is_td(d) -> bool:
    from modules.jp_calendar import is_trading_day
    return is_trading_day(d)
