"""検証の結果を、同じ読み方で並べるための道具。

検証ごとに表の形が変わると、前の検証と比べられない。
「相関 → コホート別 → 5分位 → 足切り」の4点セットに統一する。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

NOISE = 0.05   # これ未満はノイズとみなす
REAL = 0.10    # これを超えたら意味がある可能性


def hr(title: str) -> None:
    print("\n" + "─" * 84 + f"\n{title}\n" + "─" * 84)


def corr(df: pd.DataFrame, x: str, y: str, minimum: int = 100) -> float:
    m = df[x].notna() & df[y].notna()
    if m.sum() < minimum:
        return np.nan
    return float(spearmanr(df.loc[m, x], df.loc[m, y]).statistic)


def corr_table(df: pd.DataFrame, factors: dict[str, str],
               outcomes: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame({fn: {on: corr(df, fc, oc) for on, oc in outcomes.items()}
                         for fn, fc in factors.items()}).T


def cohort_table(frames: dict, factors: dict[str, str], outcome: str) -> pd.DataFrame:
    return pd.DataFrame({k: {fn: corr(v, fc, outcome, 50) for fn, fc in factors.items()}
                         for k, v in frames.items()})


def verdict(row: pd.Series) -> str:
    """コホートをまたいだ安定性から、採否の下書きを出す。"""
    v = row.dropna()
    if len(v) < 2:
        return "判定不能"
    if not (v > 0).all() and not (v < 0).all():
        return "❌ 符号が入れ替わる＝効いていない"
    if (v.abs() >= REAL).sum() >= 2:
        return "✅ 同符号で2つ以上が 0.10 超"
    if v.abs().max() < NOISE:
        return "△ 同符号だが全部ノイズ水準"
    return "△ 同符号だが弱い"


def quintiles(df: pd.DataFrame, factor: str, outcomes: dict[str, str],
              minimum: int = 300) -> pd.DataFrame | None:
    m = df[factor].notna()
    if m.sum() < minimum:
        return None
    q = pd.qcut(df.loc[m, factor].rank(method="first"), 5,
                labels=[f"Q{i}" for i in range(1, 6)])
    agg = {n: (c, "mean" if df[c].dropna().isin([0, 1]).all() else "median")
           for n, c in outcomes.items() if df[c].notna().any()}
    g = df[m].groupby(q, observed=True).agg(n=(factor, "count"), **agg)
    return g


def show_quintiles(df: pd.DataFrame, factors: dict[str, str],
                   outcomes: dict[str, str]) -> None:
    for fn, fc in factors.items():
        g = quintiles(df, fc, outcomes)
        if g is None:
            print(f"\n  【{fn}】データが足りない")
            continue
        print(f"\n  【{fn}】Q5 が最上位  n={int(g['n'].mean()):,}/分位")
        for on in outcomes:
            if on not in g.columns:
                continue
            print(f"    {on:<10s}" + "  ".join(f"{i}:{v:+.0%}" for i, v in
                                               zip(g.index, g[on])))


def gate_effect(df: pd.DataFrame, base_mask: pd.Series,
                gates: list[tuple[str, pd.Series]],
                outcomes: dict[str, str], minimum: int = 30) -> None:
    """足切りを1つずつ重ねたときに、実績がどう動くか。"""
    m = base_mask.copy()
    rows = [("足切りなし", m)]
    for name, g in gates:
        m = m & g.fillna(False)
        rows.append((name, m.copy()))
    for name, mask in rows:
        s = df[mask]
        if len(s) < minimum:
            print(f"    {name:<30s} n={len(s):>5,}  （少なすぎる）")
            continue
        parts = []
        for on, oc in outcomes.items():
            v = s[oc].dropna()
            if v.empty:
                continue
            stat = v.mean() if v.isin([0, 1]).all() else v.median()
            parts.append(f"{on} {stat:>+7.1%}")
        print(f"    {name:<30s} n={len(s):>5,}  " + "  ".join(parts))


def split_halves(frames: dict) -> tuple[pd.DataFrame, pd.DataFrame, list, list]:
    """前半で見て、後半で答え合わせするための分け方。"""
    ks = sorted(frames)
    h = max(1, len(ks) // 2)
    tr = pd.concat([frames[k] for k in ks[:h]], ignore_index=True)
    te = pd.concat([frames[k] for k in ks[h:]], ignore_index=True)
    return tr, te, ks[:h], ks[h:]
