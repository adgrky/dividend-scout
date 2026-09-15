"""B層（増配意思）の検証 — 「減益でも配当を守った実績」は、連続増配より強いか。

【問題意識】
ヘムの型は「減配歴なし」を条件にする。本検証でも連続増配は減配を減らした
（49.2% 対 60.6%）。だが**減配歴がないだけの会社には、そもそも減益を一度も
経験していない幸運な会社が混ざる**。それは意思の証拠ではない。

そこで、有報の純利益と1株配当を突き合わせて、こう定義する。

    守った率 ＝ 減益になった年のうち、配当を維持または増やした年の割合

減益という試練を実際に受けて、なお配当を守った회数で測る。これが将来の減配率を
下げるなら、手入力に頼っている「配当方針」を、数字で置き換えられる。

【比べる相手】
    連続増配年数 / 過去の減配回数 / 守った率
どれがいちばん、将来の減配を防ぐか。リターンを犠牲にしていないか。

使い方:
    uv run python scripts/validate_willingness.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

from modules import valreport as V       # noqa: E402
from modules.hist_panel import load_cached  # noqa: E402

FACTORS = {
    "B_守った率（減益年に配当維持）": "defense",
    "B_守った回数": "defended",
    "B_連続増配年数": "streak",
    "B_過去10年の減配回数（少ないほど上）": "no_cuts",
    "（比較）起点の利回り": "start_yield",
}


def add_features(p: pd.DataFrame) -> pd.DataFrame:
    """その年までの実績だけで作る。先の年は一切見ない。"""
    p = p.sort_values(["ticker", "fiscal_year"]).copy()
    g = p.groupby("ticker", sort=False)

    down = p["is_down_year"].fillna(False).astype(float)
    held = (p["is_down_year"].fillna(False) & p["dps_held"].fillna(False)).astype(float)
    cut = p["dps_cut"].fillna(False).astype(float)
    up = (p["dps_use"] > p["prev_dps"] * 1.001).fillna(False).astype(float)

    def roll(s: pd.Series) -> pd.Series:
        # shift(1) で「その年の結果」を含めない
        return s.shift(1).rolling(10, min_periods=1).sum()

    p["n_down"] = g.apply(lambda d: roll(down.loc[d.index]),
                          include_groups=False).reset_index(level=0, drop=True)
    p["defended"] = g.apply(lambda d: roll(held.loc[d.index]),
                            include_groups=False).reset_index(level=0, drop=True)
    p["n_cuts"] = g.apply(lambda d: roll(cut.loc[d.index]),
                          include_groups=False).reset_index(level=0, drop=True)
    p["defense"] = np.where(p["n_down"] >= 2, p["defended"] / p["n_down"], np.nan)
    p["no_cuts"] = -p["n_cuts"]

    # 連続増配年数：その年の前年まで、増配が何年続いたか
    def streak(s: pd.Series) -> pd.Series:
        out, run = [], 0
        for v in s.shift(1).fillna(0):
            run = run + 1 if v > 0 else 0
            out.append(run)
        return pd.Series(out, index=s.index)
    p["streak"] = g.apply(lambda d: streak(up.loc[d.index]),
                          include_groups=False).reset_index(level=0, drop=True)
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy", default="2014,2016,2018,2020")
    ap.add_argument("--horizon", type=int, default=5)
    a = ap.parse_args()
    fys = [int(x) for x in a.fy.split(",")]
    h = a.horizon
    out = {"DPS成長": f"dps_growth_{h}y", "リターン": f"ret_{h}y", "減配の発生": f"cut_{h}y"}

    print(f"B層（増配意思）の検証  起点 FY{fys} ／ 先 {h} 年")
    p = add_features(load_cached())
    p = p[p[f"ret_{h}y"].notna() & (p["start_yield"] > 0)]

    frames = {}
    for fy in fys:
        c = p[p["fiscal_year"] == fy]
        if len(c) < 100:
            print(f"  FY{fy}: {len(c)} 銘柄しかない。除外します")
            continue
        n_def = c["defense"].notna().sum()
        print(f"  FY{fy}: {len(c):,} 銘柄（うち守った率が測れる＝減益を2回以上経験 {n_def:,}）")
        frames[fy] = c
    if not frames:
        print("コホートが作れませんでした")
        return
    df = pd.concat(frames.values(), ignore_index=True)

    V.hr("1. 各指標と実績の順位相関")
    print(V.corr_table(df, FACTORS, out).round(3).to_string())

    if len(frames) > 1:
        V.hr("2. コホート別 — 減配の発生（安定しているか）")
        t = V.cohort_table(frames, FACTORS, f"cut_{h}y")
        t["判定"] = t.apply(V.verdict, axis=1)
        print(t.round(3).to_string())

    V.hr("3. 守った率の帯ごとの実績")
    d = df[df["defense"].notna()].copy()
    d["帯"] = pd.cut(d["defense"], [-.01, .34, .67, .99, 1.01],
                     labels=["3割以下", "3〜7割", "7〜10割", "全部守った"])
    g = d.groupby("帯", observed=True).agg(
        n=("ticker", "count"), 減配率=(f"cut_{h}y", "mean"),
        リターン=(f"ret_{h}y", "median"), DPS成長=(f"dps_growth_{h}y", "median"),
        起点の利回り=("start_yield", "median"))
    print(g.assign(**{c: (g[c] * 100).round(1) for c in g.columns if c != "n"}).to_string())

    V.hr("4. 「減配歴なし」と「守った率10割」はどう違うか")
    base = df[f"cut_{h}y"].mean()
    tests = {
        "全体": pd.Series(True, index=df.index),
        "減配歴ゼロ": df["n_cuts"] == 0,
        "  うち 減益を2回以上経験": (df["n_cuts"] == 0) & (df["n_down"] >= 2),
        "  うち 減益の経験なし（運が良いだけ）": (df["n_cuts"] == 0) & (df["n_down"].fillna(0) < 2),
        "守った率 10割（減益2回以上）": df["defense"] >= 0.999,
        "連続増配5年以上": df["streak"] >= 5,
    }
    for name, m in tests.items():
        s = df[m.fillna(False)]
        if len(s) < 30:
            print(f"    {name:<36s} n={len(s):>5,}  （少なすぎる）")
            continue
        print(f"    {name:<36s} n={len(s):>5,}  減配率 {s[f'cut_{h}y'].mean():>5.1%}"
              f"（リフト {s[f'cut_{h}y'].mean()/base:>4.2f}）"
              f"  リターン {s[f'ret_{h}y'].median():>+6.1%}"
              f"  利回り {s['start_yield'].median():>5.2%}")
    print("\n  「減益の経験なし」と「守った率10割」で減配率が変わるなら、"
          "\n  減配歴ゼロという条件は**試練を受けた会社とそうでない会社を混ぜている**ことになる。")

    V.hr("5. 高利回り × 守った率 の足切り")
    for label, sub in [("全コホート", df)] + [(f"FY{k}", v) for k, v in frames.items()]:
        print(f"\n  【{label}】")
        hi = sub["start_yield"] >= sub["start_yield"].quantile(0.7)
        V.gate_effect(sub, hi, [
            ("  ＋ 減配歴ゼロ", sub["n_cuts"] == 0),
            ("  ＋ 守った率7割以上", sub["defense"] >= 0.67),
        ], out)

    if len(frames) >= 2:
        V.hr("6. 前半で見て、後半で答え合わせ")
        tr, te, ktr, kte = V.split_halves(frames)
        print(f"  学習 FY{ktr}（{len(tr):,}）／ 検証 FY{kte}（{len(te):,}）")
        for nm, sub in (("学習", tr), ("検証", te)):
            print(f"\n  【{nm}】")
            hi = sub["start_yield"] >= sub["start_yield"].quantile(0.7)
            V.gate_effect(sub, hi, [
                ("  ＋ 減配歴ゼロ", sub["n_cuts"] == 0),
                ("  ＋ 守った率7割以上", sub["defense"] >= 0.67),
            ], out)


if __name__ == "__main__":
    main()
