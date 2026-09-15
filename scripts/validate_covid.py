"""検証5 — コロナの減配を、事前の財務から当てられるか。そして今の保有に当てはめる。

【なぜやるか】
modules/income_risk.py のストレステストは「当時その銘柄が実際にいくら減らしたか」
という実績を引いている。正直で強い方法だが、**当時まだ配当を出していなかった銘柄には
何も言えない**。今の保有114銘柄のうち、リーマンやコロナを配当込みでくぐった銘柄しか
評価できない。

そこで逆から攻める。「どんな財務の会社がコロナで減配したか」を先に学び、その形を
今の保有に当てはめる。実績が無い銘柄にも推計が出せるようになる。

【やり方】
    学習 … 平時（FY2014〜2018）の財務で「翌年の減配」を当てる形を作る
    検証 … その形を FY2019（コロナ直前）に当てて、FY2020の減配を当てられたか見る
           平時で作った形がショック時にも通用するかは、やってみないと分からない
    適用 … 同じ形を今の保有に当てて、コロナ級の一撃で配当が何割減るかを出す

【読み方】
平時で作った形がコロナで通用しなければ、「ショックは財務では読めない」という
結論になる。それはそれで意味がある（＝分散で守るしかない、という話になる）。

使い方:
    uv run python scripts/validate_covid.py
    uv run python scripts/validate_covid.py --apply   # 今の保有に当てはめる
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                   # noqa: E402
import pandas as pd                                  # noqa: E402

from modules import valreport as V                   # noqa: E402
from modules.config import load_config               # noqa: E402
from modules.hist_panel import load_cached           # noqa: E402
from modules.store import read_df                    # noqa: E402

# 検証2（scripts/validate_cut_signals.py）で学習・検証の両方で効いた兆候だけを使う。
# 効かなかったものを「理屈に合うから」と入れないこと。
# コロナの年度。3月期の会社の「2020年3月期」＝コロナ直前までの決算。
# ここを起点にした1年以内の減配が、コロナ減配にあたる。
COVID_FY = 2020

RULES = {
    "配当性向70%超": lambda d: d["payout"] > 0.70,
    "配当性向が1年で20pt上昇": lambda d: d["payout_up"] >= 0.20,
    "利回りが上位10%": lambda d: d["yield_rank"] >= 0.90,
    "減益3割超": lambda d: d["ni_drop"] >= 0.30,
    "最終赤字": lambda d: d["net_income"] < 0,
}


def add_features(p: pd.DataFrame) -> pd.DataFrame:
    p = p.sort_values(["ticker", "fiscal_year"]).copy()
    g = p.groupby("ticker", sort=False)
    one = (p["fiscal_year"] - g["fiscal_year"].shift(1)) == 1
    p["payout_up"] = p["payout"] - g["payout"].shift(1).where(one)
    prev_ni = g["net_income"].shift(1).where(one)
    with np.errstate(divide="ignore", invalid="ignore"):
        p["ni_drop"] = np.where(prev_ni > 0, 1 - p["net_income"] / prev_ni, np.nan)
    p["yield_rank"] = p.groupby("fiscal_year")["start_yield"].rank(pct=True)
    p["n_flags"] = sum(f(p).fillna(False).astype(int) for f in RULES.values())
    return p


def cut_rate_by_flags(df: pd.DataFrame, target: str) -> pd.DataFrame:
    g = df.groupby(df["n_flags"].clip(upper=3))[target]
    t = g.agg(["count", "mean"]).rename(columns={"count": "n", "mean": "減配率"})
    t.index = [f"{i}個" + ("以上" if i == 3 else "") for i in t.index]
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="今の保有に当てはめる")
    a = ap.parse_args()

    p = add_features(load_cached())
    print("検証5 — コロナの減配は、事前の財務から読めたか")

    V.hr("1. コロナの年は、ほんとうに特別だったか")
    t = (p[p["cut_1y"].notna()].groupby("fiscal_year")
         .agg(n=("ticker", "count"), 翌年の減配率=("cut_1y", "mean")))
    t = t[t["n"] >= 100]
    t["翌年の減配率"] = (t["翌年の減配率"] * 100).round(1)
    print(t.to_string())
    print("\n  FY2019 の行が「コロナで減配した割合」。平時の年と比べる。")

    V.hr("2. 平時で作った形は、コロナに通用したか")
    train = p[p["fiscal_year"].between(2014, 2018) & p["cut_1y"].notna()]
    covid = p[(p["fiscal_year"] == COVID_FY) & p["cut_1y"].notna()]
    if len(covid) < 100:
        print(f"  FY2019 が {len(covid)} 件しかありません。有報の取り込みが済んでから"
              "もう一度実行してください。")
    else:
        for name, sub in (("平時 FY2014〜2018", train), (f"コロナ FY{COVID_FY}→{COVID_FY+1}", covid)):
            base = sub["cut_1y"].mean()
            print(f"\n  【{name}】ベース率 {base:.1%}  n={len(sub):,}")
            for rn, fn in RULES.items():
                m = fn(sub).fillna(False).astype(bool)
                if m.sum() < 20:
                    print(f"    {rn:<24s} 発火 {int(m.sum()):>4,}  （少なすぎる）")
                    continue
                r = sub.loc[m, "cut_1y"].mean()
                print(f"    {rn:<24s} 発火 {int(m.sum()):>4,}  減配率 {r:>5.1%}"
                      f"  リフト {r/base:>4.2f}")
            print("    ── 兆候の数で見ると ──")
            tt = cut_rate_by_flags(sub, "cut_1y")
            for i, r in tt.iterrows():
                print(f"    兆候 {i:<6s} n={int(r['n']):>5,}  減配率 {r['減配率']:>5.1%}"
                      f"  リフト {r['減配率']/base:>4.2f}")

    V.hr("3. 業種は、ショックのときだけ効くのか")
    # 平時には業種で減配は読めなかった（本検証で棄却済み）。ショック時は別かもしれない。
    uni = read_df("SELECT ticker, sector33 FROM universe")
    q = p.merge(uni, on="ticker", how="left")
    for fy, label in ((COVID_FY, f"コロナ FY{COVID_FY}→{COVID_FY+1}"),):
        s = q[(q["fiscal_year"] == fy) & q["cut_1y"].notna()]
        if len(s) < 200:
            print(f"  {label}: {len(s)} 件。取り込みが済んでから再実行してください。")
            continue
        g = (s.groupby("sector33").agg(n=("ticker", "count"), 減配率=("cut_1y", "mean"))
             .query("n >= 15").sort_values("減配率", ascending=False))
        base = s["cut_1y"].mean()
        print(f"  {label}  全体 {base:.1%}\n")
        print("  減配が多かった業種"); print((g.head(8).assign(
            減配率=lambda d: (d["減配率"] * 100).round(1))).to_string())
        print("\n  減配が少なかった業種"); print((g.tail(8).assign(
            減配率=lambda d: (d["減配率"] * 100).round(1))).to_string())

    if not a.apply:
        return

    V.hr("4. 今の保有に当てはめる")
    from modules.portfolio import load_positions
    cfg = load_config()
    pos = load_positions(cfg)
    if pos.empty:
        print("  保有がありません")
        return
    latest = (p.sort_values("fiscal_year").groupby("ticker").tail(1)
              .set_index("ticker"))
    rows = []
    for r in pos.itertuples(index=False):
        f = latest["n_flags"].get(r.ticker, np.nan)
        rows.append(f)
    pos = pos.copy()
    pos["兆候の数"] = rows
    year_income = pos.get("年間配当", pos.get("年間配当額"))
    if year_income is None:
        print("  保有の年間配当が取れませんでした")
        return
    base = covid["cut_1y"].mean() if len(covid) >= 100 else np.nan
    print(f"  保有 {len(pos)} 銘柄のうち、兆候が取れたのは {pos['兆候の数'].notna().sum()} 銘柄")
    g = pos.groupby(pos["兆候の数"].fillna(-1)).agg(
        銘柄数=("ticker", "count"))
    print(g.to_string())
    print("\n  ※ この節は、コロナ時の減配率を兆候の数ごとに当てはめて"
          "配当の目減りを見積もるためのもの。")


if __name__ == "__main__":
    main()
