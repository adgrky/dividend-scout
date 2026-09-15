"""検証9 — 業種を散らすと、不況のときに配当の落ち込みは浅くなるのか。

【なぜやるか】
業種は **平時の減配を予測しない**（2026-09-15 の検証で棄却済み。業種ごとの減配率は
大きく違うのに、2014年の順位と2020年の順位の相関は 0.08）。

だが「業種で減配を当てる」のと「業種を散らすと被害が浅くなる」は別の問い。
前者は当たらなくても、後者は成り立ちうる。**業種ごとの減配が同時に起きないなら、
散らすだけで振れ幅は下がる。** アプリの業種上限20%は後者の前提に立っているが、測っていない。

【測り方】
リーマン（2007年度 → 2009〜2010年度の最低）で、実際に配当を出していた銘柄から
N銘柄のポートフォリオを1万通り作り、年間配当がどれだけ減ったかを見る。

    「散らさない」… 業種をいくつかに絞って、その中から選ぶ
    「散らす」    … 業種の上限を決めて、なるべく多くの業種にまたがるように選ぶ

同じ銘柄数で比べる（銘柄数を増やせば当然ぶれは下がるので、そこと混ぜない）。

使い方:
    uv run python scripts/validate_sector_shock.py
    uv run python scripts/validate_sector_shock.py --event コロナ・ショック
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                           # noqa: E402
import pandas as pd                          # noqa: E402

from modules import valreport as V           # noqa: E402
from modules.dividend_history import annual_dps   # noqa: E402
from modules.income_risk import EVENTS       # noqa: E402
from modules.store import read_df            # noqa: E402

RNG = np.random.default_rng(20260916)


def load_drops(before: int, after: tuple[int, int]) -> pd.DataFrame:
    """その不況で、各銘柄の1株配当が何割減ったか。"""
    dv = read_df("SELECT ticker, date, amount FROM dividends")
    dv["date"] = pd.to_datetime(dv["date"])
    uni = read_df("SELECT ticker, sector33 FROM universe")
    rows = []
    for t, g in dv.groupby("ticker"):
        tb = annual_dps(g[["date", "amount"]])
        if tb.empty or before not in tb.index:
            continue
        b = float(tb.loc[before, "dps"])
        if b <= 0:
            continue
        vals = [float(tb.loc[y, "dps"]) for y in range(after[0], after[1] + 1)
                if y in tb.index]
        if not vals:
            continue
        rows.append({"ticker": t, "before": b, "after": min(vals),
                     "drop": 1 - min(min(vals) / b, 1.0)})
    d = pd.DataFrame(rows).merge(uni, on="ticker", how="left")
    return d.dropna(subset=["sector33"])


def sample_spread(d: pd.DataFrame, n: int, max_per_sector: int) -> np.ndarray:
    """業種あたりの上限を守って n 銘柄選ぶ。上限が大きいほど「散らさない」。"""
    order = RNG.permutation(len(d))
    used: dict[str, int] = {}
    pick = []
    sec = d["sector33"].to_numpy()
    for i in order:
        s = sec[i]
        if used.get(s, 0) >= max_per_sector:
            continue
        used[s] = used.get(s, 0) + 1
        pick.append(i)
        if len(pick) == n:
            break
    return np.array(pick)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="リーマン・ショック")
    ap.add_argument("--trials", type=int, default=3000)
    a = ap.parse_args()
    ev = next((e for e in EVENTS if e[0] == a.event), None)
    if ev is None:
        raise SystemExit(f"知らないできごと: {a.event}")
    label, before, after, note = ev

    print(f"検証9 — 業種を散らすと不況の配当は守られるか（{label}）")
    d = load_drops(before, after)
    print(f"  当時 配当を出していた銘柄 {len(d):,} ／ 業種 {d['sector33'].nunique()}")
    print(f"  全体の減配率（銘柄ベース） {(d['drop'] > 0.001).mean():.1%}"
          f" ／ 1株配当の減り（中央値） {d['drop'].median():.1%}")

    V.hr("1. 業種ごとの落ち込み（当時の実績）")
    g = (d.groupby("sector33").agg(n=("ticker", "count"), 減った割合=("drop", "mean"))
         .query("n >= 10").sort_values("減った割合", ascending=False))
    g["減った割合"] = (g["減った割合"] * 100).round(1)
    print("  ひどかった業種"); print(g.head(6).to_string())
    print("\n  軽かった業種"); print(g.tail(6).to_string())
    print(f"\n  いちばんひどい業種と軽い業種の差 "
          f"{g['減った割合'].max() - g['減った割合'].min():.0f}ポイント")

    V.hr("2. 同じ銘柄数で、業種の散らし方だけ変える")
    print("  1銘柄あたりの配当額は同じとみなして、単純平均で見る（等金額で持った場合）")
    for n in (20, 40, 80):
        print(f"\n  【{n}銘柄のポートフォリオ】")
        print(f"    {'業種あたりの上限':<20s}{'配当の減り 中央値':>18s}{'悪いほうから5%':>16s}"
              f"{'またいだ業種数':>14s}")
        for cap in (n, max(1, n // 3), max(1, n // 6), max(1, n // 10), 2, 1):
            if cap > n:
                continue
            drops, secs = [], []
            for _ in range(a.trials):
                idx = sample_spread(d, n, cap)
                if len(idx) < n:
                    continue
                sub = d.iloc[idx]
                drops.append(sub["drop"].mean())
                secs.append(sub["sector33"].nunique())
            if len(drops) < 100:
                continue
            arr = np.array(drops)
            tag = "上限なし" if cap >= n else f"{cap}銘柄まで"
            print(f"    {tag:<20s}{np.median(arr):>17.1%}{np.percentile(arr, 95):>16.1%}"
                  f"{np.mean(secs):>14.1f}")
    print("\n  中央値がほとんど動かず、悪いほうの裾だけ縮むなら、")
    print("  業種分散の効果は『平均を良くする』ではなく『最悪を浅くする』。")

    V.hr("3. 業種を絞ったらどうなるか（最悪の引き）")
    worst = g.head(3).index.tolist()
    best = g.tail(3).index.tolist()
    for name, secs in (("ひどかった業種3つだけで40銘柄", worst),
                       ("軽かった業種3つだけで40銘柄", best)):
        sub = d[d["sector33"].isin(secs)]
        if len(sub) < 40:
            print(f"  {name}: 銘柄が足りない（{len(sub)}）")
            continue
        vals = [sub.sample(40, random_state=int(RNG.integers(1e9)))["drop"].mean()
                for _ in range(a.trials)]
        print(f"  {name:<32s} 減り 中央値 {np.median(vals):>6.1%}"
              f"／悪いほうから5% {np.percentile(vals, 95):>6.1%}")
    print("\n  業種を当てられるなら差は大きい。だが**当てられない**ことは既に確かめてある")
    print("  （業種ごとの減配率の順位は、2014年と2020年で相関 0.08）。")
    print("  当てられない以上、できるのは散らすことだけ。")


if __name__ == "__main__":
    main()
