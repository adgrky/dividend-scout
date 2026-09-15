"""検証6 — 「減配したら売る」は、本当に正しいのか。

【なぜやるか】
このアプリの売り基準の第一条は「減配したら売る」。ヘムの唯一の絶対ルールであり、
本検証でも「過去に減配した会社はまた減配する」（相関 −0.175）と裏が取れている。

だが **減配したあと株価がどう動くか** は一度も測っていない。減配は発表前から
株価に織り込まれることが多く、発表後はむしろ売られ切って反発することもある。
「また減配する」ことと「売ったほうが得」は別の話。ここを確かめずに売りを勧めていた。

【測り方】
その年度に減配した銘柄を、決算が出そろう3ヶ月後の時点で切り出し、
そこから1年・3年・5年のトータルリターン（配当込み）を、同じ日の

    ・全銘柄
    ・減配しなかった銘柄
    ・高利回り（上位30%）で減配しなかった銘柄 ＝ **乗り換え先の候補**

と比べる。乗り換え先に負けているなら「売って買い替える」は正しい。
勝っているなら、売る理由は値上がりではなく **インカムの回復** に求めるべきで、
売り基準の書き方を変えなければならない。

使い方:
    uv run python scripts/validate_after_cut.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                           # noqa: E402
import pandas as pd                          # noqa: E402

from modules import valreport as V           # noqa: E402
from modules.hist_panel import load_cached   # noqa: E402


def main() -> None:
    p = load_cached()
    p = p[p["fiscal_year"].between(2014, 2022) & (p["start_yield"] > 0)].copy()
    p["yr"] = p.groupby("fiscal_year")["start_yield"].rank(pct=True)
    p["cut_now"] = p["dps_cut"].fillna(False).astype(bool)
    n_cut = int(p["cut_now"].sum())
    print("検証6 — 減配したあと、売るべきか")
    print(f"  観測 延べ {len(p):,} 銘柄・年 ／ うちその年に減配 {n_cut:,}")

    V.hr("1. 減配した年のあと、どうなったか（配当込みのリターン・中央値）")
    groups = {
        "その年に減配した": p["cut_now"],
        "減配しなかった": ~p["cut_now"],
        "高利回り上位30%で減配なし（乗り換え先）": (~p["cut_now"]) & (p["yr"] >= 0.70),
        "全銘柄": pd.Series(True, index=p.index),
    }
    hdr = f"  {'':<38s}" + "".join(f"{h:>12s}" for h in ("1年", "3年", "5年"))
    print(hdr)
    for name, m in groups.items():
        s = p[m]
        cells = []
        for h in (1, 3, 5):
            v = s[f"ret_{h}y"].dropna()
            cells.append(f"{v.median():>+11.1%}" if len(v) >= 50 else "        —")
        n = int(p.loc[m, "ret_3y"].notna().sum())
        print(f"  {name:<38s}" + " ".join(cells) + f"   n={n:,}")

    V.hr("2. 同じ年・同じ利回り帯で比べる（年の巡り合わせを消す）")
    # 減配した年は市場全体が悪いことが多い。年をそろえないと、減配のせいなのか
    # 相場のせいなのか分からない。
    rows = []
    for h in (1, 3, 5):
        col = f"ret_{h}y"
        d = p[p[col].notna()].copy()
        if d.empty:
            continue
        d["帯"] = pd.cut(d["yr"], [0, .5, .7, .85, 1.0],
                         labels=["下位50%", "50-70%", "70-85%", "上位15%"])
        g = d.groupby(["fiscal_year", "帯", "cut_now"], observed=True)[col].median().unstack()
        if True not in g.columns or False not in g.columns:
            continue
        diff = (g[True] - g[False]).dropna()
        rows.append({"先": f"{h}年", "比べた組": len(diff),
                     "減配した側の差（中央値）": diff.median(),
                     "減配した側が勝った割合": float((diff > 0).mean())})
    t = pd.DataFrame(rows)
    if not t.empty:
        t["減配した側の差（中央値）"] = (t["減配した側の差（中央値）"] * 100).round(1)
        t["減配した側が勝った割合"] = (t["減配した側が勝った割合"] * 100).round(1)
        print(t.to_string(index=False))
        print("\n  差がマイナスなら、同じ年・同じ利回り帯の中でも減配した側が負けている。")

    V.hr("3. 減配したあと、また減配するか")
    base = p.loc[~p["cut_now"], "cut_3y"].mean()
    after = p.loc[p["cut_now"], "cut_3y"].mean()
    print(f"  減配しなかった銘柄の、その後3年の減配率  {base:.1%}")
    print(f"  減配した銘柄の、その後3年の減配率      {after:.1%}（リフト {after/base:.2f}）")

    V.hr("4. 配当は戻るか（減配した年を1.0として、その後の1株配当）")
    p2 = p.sort_values(["ticker", "fiscal_year"])
    g = p2.groupby("ticker", sort=False)
    out = {}
    for k in (1, 2, 3):
        ahead = g["dps_use"].shift(-k)
        ok = (g["fiscal_year"].shift(-k) - p2["fiscal_year"]) == k
        r = (ahead / p2["dps_use"]).where(ok & (p2["dps_use"] > 0))
        out[f"{k}年後"] = r[p2["cut_now"]].median()
        out[f"{k}年後（減配なし）"] = r[~p2["cut_now"]].median()
    print("  減配した銘柄     " + "  ".join(
        f"{k}:{out[f'{k}年後']:.2f}倍" for k in (1, 2, 3)))
    print("  減配しなかった銘柄 " + "  ".join(
        f"{k}:{out[f'{k}年後（減配なし）']:.2f}倍" for k in (1, 2, 3)))

    V.hr("5. 減配の深さで分けると")
    d = p[p["cut_now"] & (p["prev_dps"] > 0)].copy()
    d["深さ"] = 1 - d["dps_use"] / d["prev_dps"]
    d["帯"] = pd.cut(d["深さ"], [0, .15, .3, .5, 1.01],
                     labels=["1〜15%減", "15〜30%減", "30〜50%減", "50%超減・無配"])
    g = d.groupby("帯", observed=True).agg(
        n=("ticker", "count"), リターン3年=("ret_3y", "median"),
        リターン5年=("ret_5y", "median"), また減配=("cut_3y", "mean"))
    for c in ("リターン3年", "リターン5年", "また減配"):
        g[c] = (g[c] * 100).round(1)
    print(g.to_string())
    print("\n  浅い減配と、無配転落を同じ『減配』として扱ってよいかを見る。")


if __name__ == "__main__":
    main()
