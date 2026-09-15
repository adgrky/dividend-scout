"""スコアの予測力を過去データで確かめる。

【なぜ必ずやるか】
別プロジェクト（stock-recommender）で、13個の銘柄選別指標を測ったら相関が
すべて |r| < 0.1 で、「銘柄選別に上乗せできる情報は無い」という結果が出ている。
配当成長は時間軸が5〜10年と違うので同じ結論とは限らないが、確かめずに信じたら
同じ失敗になる。きれいなスコアが並ぶだけのアプリにしないために、
**重みを決める前にここを通す**。

【何を検証できて、何ができないか】
過去時点の特徴量を先読みなしで作れるのは、株価と配当履歴から計算できるものだけ。
yfinance の財務諸表は4〜5期分しか取れないため、2016年時点の配当性向や営業CFは
再現できない。したがって:

    検証できる    B 増配意思 / E 割安 / D 見過ごされ度（売買代金の薄さのみ）
    検証できない  A 増配余力 / C 原資成長 → Phase 3 で EDINET を入れてから

検証できない層を「効くはず」で重み付けするのは、やってはいけないことの見本なので、
そうした層は等ウェイトのまま据え置き、検証済みの層だけ重みを動かす。

【学習と検証の分け方】
前半のコホート（2014・2016年時点）で重みを決め、後半（2018・2020年時点）で
答え合わせする。全期間の最良値でチューニングすると、後から何の意味もない
数字が出てくる。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from modules.dividend_history import build_profile

# 検証できる指標だけを列挙する。名前は「どの層に属するか」を接頭辞で示す。
TESTABLE_FACTORS = {
    "B_連続増配年数": "streak",
    "B_減配なし継続年数": "streak_no_cut",
    "B_DPS5年成長": "cagr_5y",
    "B_DPS10年成長": "cagr_10y",
    "B_10年減配回数": "cuts_10y_neg",       # 少ないほど良いので符号反転
    "D_売買代金の薄さ": "turnover_neg",
    "E_自己利回りパーセンタイル": "yield_percentile",
    "E_絶対利回り": "dividend_yield",
}

OUTCOMES = {
    "DPS成長（5年）": "fwd_dps_growth",
    "トータルリターン（5年）": "fwd_total_return",
    "減配の発生": "fwd_had_cut",
}


@dataclass
class Cohort:
    asof: pd.Timestamp
    horizon_years: int = 5


def _weekly_series(prices: pd.DataFrame) -> pd.Series:
    return pd.Series(prices["close"].values, index=pd.to_datetime(prices["date"])).sort_index()


def build_asof_features(ticker: str, prices: pd.Series, div: pd.DataFrame,
                        asof: pd.Timestamp, window_years: int = 7) -> dict | None:
    """asof 時点で判明していた情報だけで特徴量を作る（先読み厳禁）。"""
    px = prices[prices.index <= asof]
    dv = div[div["date"] <= asof]
    if len(px) < 52 * 3 or dv.empty:
        return None

    prof = build_profile(ticker, dv)
    if prof.years_paying < 5 or prof.dps_latest <= 0:
        return None

    price_now = float(px.iloc[-1])
    if price_now <= 0:
        return None

    # 自己ヒストリカル利回りパーセンタイル（asof 時点までの分布で）
    cum = dv.set_index("date")["amount"].cumsum()
    idx = px.index
    cum_at = cum.reindex(cum.index.union(idx)).ffill().reindex(idx).fillna(0.0)
    prev_idx = pd.DatetimeIndex(idx - pd.DateOffset(years=1))
    cum_prev = cum.reindex(cum.index.union(prev_idx)).ffill().reindex(prev_idx).fillna(0.0)
    ttm = pd.Series(cum_at.values - cum_prev.values, index=idx).clip(lower=0)
    y = (ttm / px).replace([np.inf, -np.inf], np.nan).dropna()
    hist = y[(y.index >= asof - pd.DateOffset(years=window_years)) & (y > 0)]
    cur_y = float(y.iloc[-1]) if len(y) else np.nan
    pctile = float((hist < cur_y).mean()) if len(hist) >= 52 and cur_y > 0 else np.nan

    turnover = float(px.tail(20).mean())   # 週足終値の平均を規模の代用にする

    return {
        "ticker": ticker,
        "streak": prof.streak,
        "streak_no_cut": prof.streak_no_cut,
        "cagr_5y": prof.cagr_5y,
        "cagr_10y": prof.cagr_10y,
        "cuts_10y_neg": -prof.cuts_10y,
        "turnover_neg": -turnover,
        "yield_percentile": pctile,
        "dividend_yield": cur_y,
        "_price": price_now,
        "_dps": prof.dps_latest,
    }


def build_outcomes(ticker: str, prices: pd.Series, div: pd.DataFrame,
                   asof: pd.Timestamp, feat: dict, horizon_years: int = 5) -> dict | None:
    """asof から horizon 年後の実績。"""
    end = asof + pd.DateOffset(years=horizon_years)
    px_fwd = prices[(prices.index > asof) & (prices.index <= end)]
    if px_fwd.empty or prices.index.max() < end - pd.Timedelta(days=45):
        return None  # 期間が満了していない銘柄は検証に入れない（上場廃止も含む）

    dv_fwd = div[(div["date"] > asof) & (div["date"] <= end)]
    price_end = float(px_fwd.iloc[-1])
    divs_received = float(dv_fwd["amount"].sum())

    prof_end = build_profile(ticker, div[div["date"] <= end])
    dps_end = prof_end.dps_latest
    dps_start = feat["_dps"]

    # 期間中に減配があったか（記念配当のスパイクは除外済みの系列で見る）
    series = pd.Series(prof_end.series).sort_index()
    in_window = series[(series.index > asof.year) & (series.index <= end.year)]
    had_cut = bool((in_window.diff().dropna() < 0).any())

    return {
        "fwd_dps_growth": (dps_end / dps_start - 1) if dps_start > 0 else np.nan,
        "fwd_total_return": (price_end + divs_received) / feat["_price"] - 1,
        "fwd_had_cut": float(had_cut),
    }


def run_cohort(prices_all: pd.DataFrame, div_all: pd.DataFrame, cohort: Cohort,
               min_names: int = 100) -> pd.DataFrame:
    """1つの起点日について、特徴量と5年後の実績を突き合わせる。"""
    div_all = div_all.copy()
    div_all["date"] = pd.to_datetime(div_all["date"])
    div_by = {t: g for t, g in div_all.groupby("ticker", sort=False)}

    rows = []
    for ticker, g in prices_all.groupby("ticker", sort=False):
        dv = div_by.get(ticker)
        if dv is None or dv.empty:
            continue
        px = _weekly_series(g)
        feat = build_asof_features(ticker, px, dv, cohort.asof)
        if feat is None:
            continue
        out = build_outcomes(ticker, px, dv, cohort.asof, feat, cohort.horizon_years)
        if out is None:
            continue
        rows.append({**feat, **out, "asof": cohort.asof.strftime("%Y-%m-%d")})

    df = pd.DataFrame(rows)
    if len(df) < min_names:
        return pd.DataFrame()
    return df


def factor_power(df: pd.DataFrame) -> pd.DataFrame:
    """各指標が将来の実績をどれだけ説明するか（順位相関）。

    ピアソンではなくスピアマンを使う。配当成長も株価リターンも分布が歪んでおり、
    外れ値1銘柄で相関が決まってしまうため。
    """
    rows = []
    for label, col in TESTABLE_FACTORS.items():
        if col not in df.columns:
            continue
        for oname, ocol in OUTCOMES.items():
            sub = df[[col, ocol]].dropna()
            if len(sub) < 50:
                rows.append({"指標": label, "実績": oname, "相関": np.nan, "n": len(sub)})
                continue
            r = sub[col].corr(sub[ocol], method="spearman")
            rows.append({"指標": label, "実績": oname, "相関": float(r), "n": len(sub)})
    return pd.DataFrame(rows)


def quintile_table(df: pd.DataFrame, factor_col: str, outcome_col: str,
                   q: int = 5) -> pd.DataFrame:
    """分位ごとの実績。相関が小さくても上位分位だけ効いていることがある。"""
    sub = df[[factor_col, outcome_col]].dropna()
    if len(sub) < q * 10:
        return pd.DataFrame()
    try:
        sub["分位"] = pd.qcut(sub[factor_col].rank(method="first"), q,
                             labels=[f"Q{i + 1}" for i in range(q)])
    except ValueError:
        return pd.DataFrame()
    g = sub.groupby("分位", observed=True)[outcome_col].agg(["mean", "median", "count"])
    return g.rename(columns={"mean": "平均", "median": "中央値", "count": "銘柄数"}).reset_index()


def benchmark(df: pd.DataFrame, top_n: int = 30) -> pd.DataFrame:
    """比較対象。これに勝てないスコアは採用しない。"""
    rows = []
    rows.append({"戦略": "全銘柄を等分で持つ", "銘柄数": len(df),
                 "トータルリターン": df["fwd_total_return"].mean(),
                 "DPS成長": df["fwd_dps_growth"].median(),
                 "減配発生率": df["fwd_had_cut"].mean()})
    hi = df.nlargest(top_n, "dividend_yield")
    rows.append({"戦略": f"単純に利回り上位{top_n}銘柄", "銘柄数": len(hi),
                 "トータルリターン": hi["fwd_total_return"].mean(),
                 "DPS成長": hi["fwd_dps_growth"].median(),
                 "減配発生率": hi["fwd_had_cut"].mean()})
    st = df.nlargest(top_n, "streak")
    rows.append({"戦略": f"連続増配年数 上位{top_n}銘柄", "銘柄数": len(st),
                 "トータルリターン": st["fwd_total_return"].mean(),
                 "DPS成長": st["fwd_dps_growth"].median(),
                 "減配発生率": st["fwd_had_cut"].mean()})
    return pd.DataFrame(rows)


def composite_test(train: pd.DataFrame, test: pd.DataFrame,
                   outcome: str = "fwd_total_return", top_n: int = 30) -> pd.DataFrame:
    """学習コホートで効いた指標だけを等ウェイト合成し、検証コホートで確かめる。

    重みを細かく最適化しない。相関の順位だけを使って「効いた指標を等分で足す」に
    留める。少ないコホートで重みまで当てにいくと、確実に過剰適合する。
    """
    power = factor_power(train)
    keep = power[(power["実績"] == OUTCOMES_INV[outcome]) & (power["相関"] > 0.05)]["指標"].tolist()
    if not keep:
        return pd.DataFrame([{"結果": "学習側で相関0.05を超える指標が無かった。合成しても意味がない。"}])

    cols = [TESTABLE_FACTORS[k] for k in keep]
    z = test[cols].rank(pct=True)
    score = z.mean(axis=1)
    sel = test.assign(_score=score).nlargest(top_n, "_score")

    return pd.DataFrame([{
        "採用した指標": "、".join(keep),
        "検証コホートの上位銘柄数": len(sel),
        "トータルリターン（上位）": sel[outcome].mean(),
        "トータルリターン（全体）": test[outcome].mean(),
        "差": sel[outcome].mean() - test[outcome].mean(),
        "減配発生率（上位）": sel["fwd_had_cut"].mean(),
        "減配発生率（全体）": test["fwd_had_cut"].mean(),
    }])


OUTCOMES_INV = {v: k for k, v in OUTCOMES.items()}
