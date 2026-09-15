"""減配の予兆の検証 — 売り基準と監視基準に、実測の裏付けを与える。

【なぜやるか】
modules/sell_rules.py は理屈を詰めて作ったが、「その兆候が実際に減配の何年前に
出るのか」「出たとき何割が本当に減配したのか」は一度も測っていない。
理屈で作った閾値は、理屈で作った閾値でしかない。

【測り方】
年度 fy の時点で見える情報だけを特徴量にして、その後 1年以内 / 2年以内に
減配が起きたかを当てにいく。減配の判定はアプリ本体（build_profile）と同じ。

    ベース率     … 全体で何割が減配したか
    シグナル発火時 … その兆候が出ていた会社の何割が減配したか
    リフト       … 発火時 ÷ ベース率。1.0 なら何の情報も無い
    再現率       … 実際に減配した会社のうち、何割を事前に拾えたか

【読み方】
リフトが 1.3 を超え、かつ再現率が 10% 以上ある兆候だけが、監視に値する。
リフトが高くても発火数が極端に少なければ、実務では使えない。

使い方:
    uv run python scripts/validate_cut_signals.py
    uv run python scripts/validate_cut_signals.py --horizon 2 --fy-from 2014 --fy-to 2021
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


def add_features(p: pd.DataFrame) -> pd.DataFrame:
    p = p.sort_values(["ticker", "fiscal_year"]).copy()
    g = p.groupby("ticker", sort=False)
    one = (p["fiscal_year"] - g["fiscal_year"].shift(1)) == 1
    two = (p["fiscal_year"] - g["fiscal_year"].shift(2)) == 2

    p["payout_up"] = p["payout"] - g["payout"].shift(1).where(one)
    p["equity_down"] = g["equity_ratio"].shift(1).where(one) - p["equity_ratio"]
    prev_sales = g["sales"].shift(1).where(one)
    prev2_sales = g["sales"].shift(2).where(two)
    p["sales_down2"] = (p["sales"] < prev_sales) & (prev_sales < prev2_sales)
    prev_ni = g["net_income"].shift(1).where(one)
    with np.errstate(divide="ignore", invalid="ignore"):
        p["ni_drop"] = np.where(prev_ni > 0, 1 - p["net_income"] / prev_ni, np.nan)
    p["ocf_negative"] = p["operating_cf"] < 0
    p["loss"] = p["net_income"] < 0
    # 利益は出ているのに現金が伴わない
    p["accrual_gap"] = np.where(p["net_income"] > 0,
                                1 - p["operating_cf"] / p["net_income"], np.nan)
    p["accrual_gap"] = pd.Series(p["accrual_gap"]).clip(-5, 5)
    # 過去に減配したことがあるか（その年まで）
    p["past_cuts"] = g["dps_cut"].transform(
        lambda s: s.fillna(False).shift(1).rolling(10, min_periods=1).sum())
    return p


SIGNALS = {
    "配当性向が70%超": lambda d: d["payout"] > 0.70,
    "配当性向が100%超": lambda d: d["payout"] > 1.00,
    "配当性向が1年で20pt以上上がった": lambda d: d["payout_up"] >= 0.20,
    "営業CFが配当の2倍未満": lambda d: d["ocf_cover"] < 2,
    "営業CFが配当を下回る": lambda d: d["ocf_cover"] < 1,
    "営業CFが赤字": lambda d: d["ocf_negative"],
    "最終赤字": lambda d: d["loss"],
    "減益が3割超": lambda d: d["ni_drop"] >= 0.30,
    "2期連続の減収": lambda d: d["sales_down2"],
    "自己資本比率が5pt以上低下": lambda d: d["equity_down"] >= 0.05,
    "自己資本比率が30%未満": lambda d: d["equity_ratio"] < 0.30,
    "利益に現金が伴っていない": lambda d: d["accrual_gap"] >= 0.5,
    "過去10年に減配歴あり": lambda d: d["past_cuts"] >= 1,
    "利回りが上位10%（高すぎる）": lambda d: d["start_yield"] >= d["start_yield"].quantile(0.90),
    "特別利益で利益が膨らんでいる": lambda d: d["ni_over_op"] >= 1.5,
}


def report(df: pd.DataFrame, label: str, target: str) -> pd.DataFrame:
    base = df[target].mean()
    rows = []
    for name, fn in SIGNALS.items():
        m = fn(df).fillna(False).astype(bool)
        n = int(m.sum())
        if n < 30:
            rows.append({"兆候": name, "発火": n, "減配率": np.nan,
                         "リフト": np.nan, "再現率": np.nan})
            continue
        rate = float(df.loc[m, target].mean())
        recall = float((m & (df[target] == 1)).sum() / max(1, (df[target] == 1).sum()))
        rows.append({"兆候": name, "発火": n, "減配率": rate,
                     "リフト": rate / base if base else np.nan, "再現率": recall})
    t = pd.DataFrame(rows).set_index("兆候").sort_values("リフト", ascending=False)
    print(f"\n  【{label}】全体の減配率（ベース率）= {base:.1%}  n={len(df):,}")
    for name, r in t.iterrows():
        if pd.isna(r["リフト"]):
            print(f"    {name:<32s} 発火 {int(r['発火']):>5,}  （少なすぎて測れない）")
            continue
        mark = "✅" if (r["リフト"] >= 1.3 and r["再現率"] >= 0.10) else \
               "△" if r["リフト"] >= 1.3 else "  "
        print(f"    {mark} {name:<30s} 発火 {int(r['発火']):>5,}  "
              f"減配率 {r['減配率']:>5.1%}  リフト {r['リフト']:>4.2f}  "
              f"再現率 {r['再現率']:>5.1%}")
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=2, help="何年以内の減配を当てるか")
    ap.add_argument("--fy-from", type=int, default=2014)
    ap.add_argument("--fy-to", type=int, default=2022)
    a = ap.parse_args()
    target = f"cut_{a.horizon}y"

    print(f"減配の予兆の検証  FY{a.fy_from}〜FY{a.fy_to} ／ {a.horizon}年以内の減配を当てる")
    p = add_features(load_cached())
    p = p[p["fiscal_year"].between(a.fy_from, a.fy_to) & p[target].notna()
          & (p["start_yield"] > 0)]
    if p.empty:
        print("データが足りません")
        return
    print(f"  観測 延べ {len(p):,} 銘柄・年 ／ 銘柄 {p['ticker'].nunique():,}")

    V.hr("1. 兆候ごとの的中（全期間）")
    t_all = report(p, "全期間", target)
    print("\n  ✅ = リフト1.3以上かつ再現率10%以上。監視に使う価値がある")
    print("  △ = よく当たるが、拾える数が少ない")

    V.hr("2. 前半で見て、後半で答え合わせ")
    mid = (a.fy_from + a.fy_to) // 2
    tr = p[p["fiscal_year"] <= mid]
    te = p[p["fiscal_year"] > mid]
    t1 = report(tr, f"学習 FY{a.fy_from}〜{mid}", target)
    t2 = report(te, f"検証 FY{mid+1}〜{a.fy_to}", target)
    both = t1[["リフト"]].join(t2[["リフト"]], lsuffix="_学習", rsuffix="_検証")
    both["判定"] = np.where(
        (both["リフト_学習"] >= 1.3) & (both["リフト_検証"] >= 1.3), "✅ 両方で効いた",
        np.where((both["リフト_学習"] >= 1.3) | (both["リフト_検証"] >= 1.3),
                 "△ 片方だけ", "❌ 効かない"))
    print("\n  【学習と検証の突き合わせ】")
    print(both.round(2).to_string())

    V.hr("3. 何年前から見えるか")
    for h in (1, 2, 3):
        col = f"cut_{h}y"
        if col not in p.columns or p[col].notna().sum() < 500:
            continue
        sub = p[p[col].notna()]
        base = sub[col].mean()
        line = []
        for name in ("配当性向が100%超", "営業CFが配当を下回る", "最終赤字",
                     "減益が3割超", "過去10年に減配歴あり"):
            m = SIGNALS[name](sub).fillna(False).astype(bool)
            if m.sum() >= 30:
                line.append(f"{name.split('が')[0][:8]} {sub.loc[m, col].mean()/base:>4.2f}")
        print(f"  {h}年以内（ベース {base:>5.1%}）  " + "  ".join(line))
    print("\n  数字はリフト。年数を延ばすとリフトが薄まる兆候は、直前にしか効かない。")

    V.hr("4. 兆候を重ねると精度は上がるか")
    n_sig = sum(SIGNALS[k](p).fillna(False).astype(int) for k in
                ("配当性向が70%超", "営業CFが配当の2倍未満", "最終赤字",
                 "減益が3割超", "2期連続の減収", "自己資本比率が5pt以上低下",
                 "過去10年に減配歴あり"))
    base = p[target].mean()
    g = p.assign(n=n_sig).groupby(pd.Series(n_sig).clip(upper=4).values)[target]
    print(f"  ベース率 {base:.1%}")
    for k, v in g.agg(["count", "mean"]).iterrows():
        lab = f"{int(k)}個" + ("以上" if k == 4 else "")
        print(f"    兆候 {lab:<6s} n={int(v['count']):>6,}  減配率 {v['mean']:>5.1%}  "
              f"リフト {v['mean']/base:>4.2f}")


if __name__ == "__main__":
    main()
