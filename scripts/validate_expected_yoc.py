"""検証12 — 「利回り」と「安全性」を別々に扱うのをやめて、期待値で一本化できるか。

【なぜやるか】
いまのアプリは、割安（利回り）と配当継続（安全性）を別の軸として計算し、
最後に相乗平均で混ぜている。混ぜ方は理屈で決めたもので、**単位が無い**。
「割安度70点・継続50点」が何を意味するのかは誰にも言えない。

だが検証2で、減配の確率が**実際の数字として**推定できるようになった。
そうすると、単位のある量が作れる。

    期待YOC ＝ いまの利回り × （5年後も配当が残っている確率）

「買値に対して、5年後にいくら受け取れそうか」。円と%で意味が通る量であり、
本検証の中心的な発見（「買値に対する配当は予測できる」）とも直接つながる。

【減配の確率をどう出すか】
検証2で学習・検証の両方で効いた3つの兆候だけを使う。前半のコホートで
「兆候の数ごとの実際の減配率」を数え、それを後半のコホートに当てはめる
（後半の答えは一切見ない）。

【比べるもの】
    ① 利回り順            … いちばん単純
    ② アプリの式           … 相乗平均 − 減点
    ③ 期待YOC順           … 利回り ×（1 − 減配確率）
    ④ 期待YOC（減配の深さも織り込む）

答え合わせは「5年後に実際に受け取っていた配当 ÷ 買値」＝**実現YOC**で見る。
リターンではなく受取配当で評価するのが、このアプリの目的に合っている。

使い方:
    uv run python scripts/validate_expected_yoc.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                           # noqa: E402
import pandas as pd                          # noqa: E402

from modules import valreport as V           # noqa: E402
from modules.hist_panel import load_cached   # noqa: E402
from modules.store import read_df            # noqa: E402

COHORTS = [2014, 2016, 2018, 2020]


def build() -> pd.DataFrame:
    p = load_cached()
    p = p[p["fiscal_year"].isin(COHORTS) & (p["start_yield"] > 0)
          & p["ret_5y"].notna()].copy()
    p = p.sort_values(["ticker", "fiscal_year"])

    full = load_cached().sort_values(["ticker", "fiscal_year"])
    g = full.groupby("ticker", sort=False)
    one = (full["fiscal_year"] - g["fiscal_year"].shift(1)) == 1
    full["payout_up"] = full["payout"] - g["payout"].shift(1).where(one)
    cuts = g["dps_cut"].transform(
        lambda s: s.fillna(False).astype(float).shift(1).rolling(10, min_periods=1).sum())
    full["past_cuts"] = cuts
    p = p.merge(full[["ticker", "fiscal_year", "payout_up", "past_cuts"]],
                on=["ticker", "fiscal_year"], how="left")

    p["yr"] = p.groupby("fiscal_year")["start_yield"].rank(pct=True)
    # 検証2で学習・検証の両方で効いた3つ
    p["兆候"] = ((p["yr"] >= 0.90).astype(int)
                + (p["payout"] > 0.70).fillna(False).astype(int)
                + (p["payout_up"] >= 0.20).fillna(False).astype(int))
    # 5年後に受け取っていた配当（買値に対して）
    p["実現YOC"] = p["start_yield"] * (1 + p["dps_growth_5y"])
    return p


def main() -> None:
    p = build()
    print("検証12 — 期待YOCで一本化できるか")
    print(f"  延べ {len(p):,} 銘柄 / コホート {COHORTS}")

    tr = p[p["fiscal_year"].isin(COHORTS[:2])]
    te = p[p["fiscal_year"].isin(COHORTS[2:])]

    V.hr("1. 前半のコホートで、兆候の数ごとの『5年後の姿』を数える")
    tbl = tr.groupby("兆候").agg(
        n=("ticker", "count"), 減配率=("cut_5y", "mean"),
        配当の伸び=("dps_growth_5y", "median"))
    print(tbl.assign(減配率=lambda x: (x.減配率 * 100).round(1),
                     配当の伸び=lambda x: (x.配当の伸び * 100).round(1)).to_string())
    print("\n  この表だけを使って、後半のコホートの期待値を作る（後半の答えは見ない）。")

    surv = (1 - tbl["減配率"]).to_dict()
    grow = tbl["配当の伸び"].to_dict()
    med_s = float(np.mean(list(surv.values())))
    med_g = float(np.mean(list(grow.values())))

    for d in (tr, te, p):
        d["生存率"] = d["兆候"].map(surv).fillna(med_s)
        d["伸び"] = d["兆候"].map(grow).fillna(med_g)
        d["③ 期待YOC"] = d["start_yield"] * d["生存率"]
        d["④ 期待YOC（伸びも込み）"] = d["start_yield"] * d["生存率"] * (1 + d["伸び"])
        d["① 利回り順"] = d["start_yield"]
        d["② 減配歴ゼロの高利回り"] = d["start_yield"].where(d["past_cuts"].fillna(9) == 0, 0)

    METHODS = ["① 利回り順", "② 減配歴ゼロの高利回り", "③ 期待YOC", "④ 期待YOC（伸びも込み）"]

    def report(d: pd.DataFrame, label: str, n: int = 30) -> None:
        print(f"\n  【{label}】各コホートで上位{n}銘柄")
        base = d
        print(f"    {'（全銘柄）':<26s} n={len(base):>5,}"
              f"  実現YOC {base['実現YOC'].median():>6.2%}"
              f"  リターン {base['ret_5y'].median():>+7.1%}"
              f"  減配率 {base['cut_5y'].mean():>5.1%}")
        for mname in METHODS:
            sub = pd.concat([g.nlargest(n, mname) for _, g in d.groupby("fiscal_year")])
            print(f"    {mname:<26s} n={len(sub):>5,}"
                  f"  実現YOC {sub['実現YOC'].median():>6.2%}"
                  f"  リターン {sub['ret_5y'].median():>+7.1%}"
                  f"  減配率 {sub['cut_5y'].mean():>5.1%}")

    V.hr("2. 学習（作った材料と同じ期間）")
    report(tr, f"学習 FY{COHORTS[:2]}")
    V.hr("3. 検証（答え合わせ。ここの数字は一切使っていない）")
    report(te, f"検証 FY{COHORTS[2:]}")

    V.hr("4. 上位何銘柄でも成り立つか（検証コホート）")
    for n in (20, 30, 50, 100):
        line = []
        for mname in ("① 利回り順", "③ 期待YOC"):
            sub = pd.concat([g.nlargest(n, mname) for _, g in te.groupby("fiscal_year")])
            line.append(f"{mname[0]} 実現YOC {sub['実現YOC'].median():.2%}"
                        f"／減配 {sub['cut_5y'].mean():.0%}")
        print(f"  上位{n:>3d}  " + "   ".join(line))

    V.hr("5. 期待YOCは、実現YOCをどれだけ当てているか")
    for label, d in (("学習", tr), ("検証", te)):
        m = d["③ 期待YOC"].notna() & d["実現YOC"].notna()
        from scipy.stats import spearmanr
        r1 = spearmanr(d.loc[m, "③ 期待YOC"], d.loc[m, "実現YOC"]).statistic
        r0 = spearmanr(d.loc[m, "start_yield"], d.loc[m, "実現YOC"]).statistic
        print(f"  {label}：期待YOC {r1:+.3f}   生の利回り {r0:+.3f}"
              f"   （差 {r1 - r0:+.3f}）")
    print("\n  期待YOCが生の利回りより当たっていなければ、"
          "\n  わざわざ確率を掛ける意味は無い。")


if __name__ == "__main__":
    main()
