"""検証10・11 — config.yaml に入っている「なんとなくの数字」を測る。

【なぜやるか】
config.yaml の冒頭には「閾値・重みはすべてここに集約する。コードに数値を直書きしない」
と書いてある。だが**集約したからといって、その数字が正しいわけではない**。
根拠なく置かれたまま一度も測っていない数字が残っている。

    scoring.yield_percentile_window_years: 7   … なぜ7年か
    gate.min_market_cap_oku: 100               … なぜ100億か
    gate.min_avg_turnover_man: 3000            … なぜ3,000万か

【測り方】
    窓の長さ … 3/5/7/10/15年で自己利回りパーセンタイルを作り直し、
               5年後のトータルリターンとの相関を比べる
    足切り   … 時価総額・売買代金の帯ごとに、5年後の実績を並べる
               （時価総額は「当時の株価 × いまの株数」の近似）

前半（2014・2016）で決めて、後半（2018・2020）で答え合わせする。

使い方:
    uv run python scripts/validate_params.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                   # noqa: E402
import pandas as pd                                  # noqa: E402
from scipy.stats import spearmanr                    # noqa: E402

from modules import valreport as V                   # noqa: E402
from modules.quality import trim_frame               # noqa: E402
from modules.store import read_df                    # noqa: E402
from modules.validation import (Cohort, build_asof_features,  # noqa: E402
                                build_outcomes, _weekly_series)

COHORTS = ["2014-12-31", "2016-12-31", "2018-12-31", "2020-12-31"]
WINDOWS = (3, 5, 7, 10, 15)


def build(horizon: int = 5) -> pd.DataFrame:
    px, _ = trim_frame(read_df("SELECT ticker, date, close, volume FROM prices"))
    px["date"] = pd.to_datetime(px["date"])
    dv = read_df("SELECT ticker, date, amount FROM dividends")
    dv["date"] = pd.to_datetime(dv["date"])
    dv_by = {t: g for t, g in dv.groupby("ticker", sort=False)}
    shares = read_df("SELECT ticker, shares FROM fundamentals "
                     "WHERE shares IS NOT NULL").groupby("ticker")["shares"].max()

    rows = []
    for asof_s in COHORTS:
        asof = pd.Timestamp(asof_s)
        for ticker, g in px.groupby("ticker", sort=False):
            d = dv_by.get(ticker)
            if d is None or d.empty:
                continue
            bars = _weekly_series(g)
            feat = build_asof_features(ticker, bars, d, asof)
            if feat is None:
                continue
            out = build_outcomes(ticker, bars, d, asof, feat, horizon)
            if out is None:
                continue
            rec = {"ticker": ticker, "asof": asof_s, **feat, **out}
            # 窓の長さを振って、自己利回りパーセンタイルを作り直す
            for w in WINDOWS:
                f2 = build_asof_features(ticker, bars, d, asof, window_years=w)
                rec[f"yp_{w}"] = f2["yield_percentile"] if f2 else np.nan
            # 当時の時価総額（いまの株数で近似）と、20週の平均売買代金
            sh = shares.get(ticker)
            rec["market_cap_oku"] = (rec["_price"] * sh / 1e8) if sh else np.nan
            w20 = g[(g["date"] <= asof) & (g["date"] > asof - pd.Timedelta(days=140))]
            rec["turnover_man"] = float((w20["close"] * w20["volume"]).mean() / 1e4 / 5) \
                if len(w20) >= 10 else np.nan
            rows.append(rec)
    return pd.DataFrame(rows)


def main() -> None:
    print("検証10・11 — config の数字を測る")
    cache = Path("data/cache_params.pkl")
    if cache.exists():
        d = pd.read_pickle(cache)
        print(f"  （前回の結果を再利用: {cache}）")
    else:
        d = build()
        d.to_pickle(cache)
    print(f"  延べ {len(d):,} 銘柄 / {d['asof'].nunique()} コホート")
    tr = d[d["asof"].isin(COHORTS[:2])]
    te = d[d["asof"].isin(COHORTS[2:])]

    V.hr("検証10. 自己利回りパーセンタイルの窓は何年が良いか")
    print(f"  {'窓':<8s}{'測れた銘柄':>10s}{'リターンとの相関':>18s}"
          f"{'学習':>10s}{'検証':>10s}{'上位30銘柄のリターン':>22s}")
    for w in WINDOWS:
        c = f"yp_{w}"
        m = d[c].notna() & d["fwd_total_return"].notna()
        if m.sum() < 500:
            continue
        # パーセンタイルは **高いほど** 自分の過去に比べて利回りが高い＝割安。
        # 符号はそのまま（正なら「割安なほどリターンが高い」）。
        r = spearmanr(d.loc[m, c], d.loc[m, "fwd_total_return"]).statistic
        mt = tr[c].notna() & tr["fwd_total_return"].notna()
        mv = te[c].notna() & te["fwd_total_return"].notna()
        rt = spearmanr(tr.loc[mt, c], tr.loc[mt, "fwd_total_return"]).statistic
        rv = spearmanr(te.loc[mv, c], te.loc[mv, "fwd_total_return"]).statistic
        top = pd.concat([g.nlargest(30, c) for _, g in d.groupby("asof")])
        print(f"  {w:>2d}年{'':<4s}{int(m.sum()):>10,}{r:>18.3f}{rt:>10.3f}{rv:>10.3f}"
              f"{top['fwd_total_return'].median():>21.1%}")
    print("\n  窓が長いほど『自分史上の安さ』は測りやすいが、"
          "\n  上場が新しい銘柄は測れなくなる（測れた銘柄の数を見る）。")

    V.hr("検証11. 時価総額と売買代金の足切りは正しいか")
    for col, unit, bins, labels, gate in (
        ("market_cap_oku", "億円", [0, 50, 100, 300, 1000, 1e9],
         ["50億未満", "50〜100億", "100〜300億", "300〜1000億", "1000億超"], 100),
        ("turnover_man", "万円", [0, 1000, 3000, 10000, 50000, 1e12],
         ["1000万未満", "1000〜3000万", "3000〜1億", "1億〜5億", "5億超"], 3000),
    ):
        s = d[d[col].notna()].copy()
        s["帯"] = pd.cut(s[col], bins, labels=labels)
        g = s.groupby("帯", observed=True).agg(
            n=("ticker", "count"), リターン=("fwd_total_return", "median"),
            DPS成長=("fwd_dps_growth", "median"), 減配率=("fwd_had_cut", "mean"),
            利回り=("dividend_yield", "median"))
        name = "時価総額" if col.startswith("market") else "20日平均売買代金"
        print(f"\n  【{name}】いまの足切りは {gate:,}{unit}")
        print(g.assign(**{c: (g[c] * 100).round(1)
                          for c in ("リターン", "DPS成長", "減配率", "利回り")}).to_string())
        below = s[s[col] < gate]
        above = s[s[col] >= gate]
        if len(below) >= 100 and len(above) >= 100:
            print(f"    足切り未満 n={len(below):,} リターン {below['fwd_total_return'].median():+.1%}"
                  f" 減配率 {below['fwd_had_cut'].mean():.1%}")
            print(f"    足切り以上 n={len(above):,} リターン {above['fwd_total_return'].median():+.1%}"
                  f" 減配率 {above['fwd_had_cut'].mean():.1%}")
        # 高利回りに重ねたとき
        hi = s["dividend_yield"] >= s.groupby("asof")["dividend_yield"].transform(
            lambda x: x.quantile(0.70))
        print("    高利回り上位30%に重ねると")
        V.gate_effect(s, hi, [(f"  ＋ {gate:,}{unit}以上", s[col] >= gate)],
                      {"リターン": "fwd_total_return", "減配率": "fwd_had_cut"})


if __name__ == "__main__":
    main()
