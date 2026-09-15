"""F層（高配当トラップ）の検証 — 配当性向が低く「見えている」だけの会社を見抜けるか。

【問題意識】
配当性向 ＝ 配当 ÷ 純利益。だがその純利益に、土地の売却益や関係会社株式の
売却益のような**一度きりの利益**が乗っていると、配当性向は実力より低く出る。
アプリはこれを traps.py で減点しているが、効くかどうかは測っていない。

【見分け方】
    純利益 ÷ 経常利益 が大きい ＝ 経常の外で利益が出ている
    本業ベースの配当性向 ＝ 配当総額 ÷（経常利益 × 0.65）
        0.65 は法人実効税率ぶんのざっくりした割引

素の配当性向では通るのに、本業ベースでは通らない会社。これが「見かけだけ安全」。
その後ほんとうに減配したのかを数える。

使い方:
    uv run python scripts/validate_traps.py
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

TAX = 0.65


def add_features(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        core = p["ordinary_income"] * TAX
        p["payout_core"] = np.where(core > 0, p["div_total"] / core, np.nan)
    p["payout_core"] = p["payout_core"].where(p["payout_core"].between(-5, 20))
    p["one_off"] = p["ni_over_op"]              # 1.0 を大きく超えるほど一時益の疑い
    p["mirage"] = (p["payout"] <= 0.50) & (p["payout_core"] > 0.70)
    p["low_yield_high_price"] = np.nan
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy-from", type=int, default=2014)
    ap.add_argument("--fy-to", type=int, default=2022)
    ap.add_argument("--horizon", type=int, default=3)
    a = ap.parse_args()
    h = a.horizon
    target = f"cut_{h}y"
    out = {"リターン": f"ret_{h}y", "減配の発生": target}

    print(f"F層（高配当トラップ）の検証  FY{a.fy_from}〜{a.fy_to} ／ {h}年以内")
    p = add_features(load_cached())
    p = p[p["fiscal_year"].between(a.fy_from, a.fy_to) & p[target].notna()
          & (p["start_yield"] > 0)]
    print(f"  観測 延べ {len(p):,} 銘柄・年 ／ 銘柄 {p['ticker'].nunique():,}")

    V.hr("1. 純利益が経常利益からどれだけ離れているか（一時益の疑い）")
    V.show_quintiles(p, {"純利益÷経常利益": "one_off"}, out)
    print("\n  Q5＝経常の外で利益が出ている。ここで減配率が高ければ、一時益は危険信号。")

    V.hr("2. 「素の配当性向」と「本業ベースの配当性向」の食い違い")
    base = p[target].mean()
    d = p[p["payout"].notna() & p["payout_core"].notna()]
    groups = {
        "どちらも50%以下（本物の余裕）": (d["payout"] <= 0.5) & (d["payout_core"] <= 0.5),
        "素は50%以下だが本業では70%超（見かけ倒し）": d["mirage"],
        "どちらも70%超（もともと苦しい）": (d["payout"] > 0.7) & (d["payout_core"] > 0.7),
    }
    print(f"  ベース率（全体の減配率）= {base:.1%}\n")
    for name, m in groups.items():
        s = d[m.fillna(False)]
        if len(s) < 30:
            print(f"    {name:<44s} n={len(s):>5,}  （少なすぎる）")
            continue
        print(f"    {name:<44s} n={len(s):>5,}  減配率 {s[target].mean():>5.1%}"
              f"（リフト {s[target].mean()/base:>4.2f}）"
              f"  リターン {s[f'ret_{h}y'].median():>+6.1%}")
    print("\n  「見かけ倒し」の減配率が「本物の余裕」より明確に高ければ、"
          "\n  配当性向の足切りは**経常利益ベースでかけるべき**ということになる。")

    V.hr("3. 高利回り × 株価が安値圏 は本当に危ないか")
    # 起点の利回りが高いほど、また利回りが自分の過去に比べて突出しているほど危ないか
    q = p.copy()
    q["yield_rank"] = q.groupby("fiscal_year")["start_yield"].rank(pct=True)
    bins = [0, .5, .7, .85, .95, 1.0]
    labels = ["下位50%", "50-70%", "70-85%", "85-95%", "上位5%"]
    q["帯"] = pd.cut(q["yield_rank"], bins, labels=labels)
    g = q.groupby("帯", observed=True).agg(
        n=("ticker", "count"), 減配率=(target, "mean"),
        リターン=(f"ret_{h}y", "median"), 利回り=("start_yield", "median"))
    print(g.assign(**{c: (g[c] * 100).round(1) for c in ("減配率", "リターン", "利回り")}).to_string())
    print("\n  上位5%だけ減配率がはね上がるなら、"
          "\n  「高利回りほど危ない」ではなく「突出した利回りだけが危ない」が正しい。")

    V.hr("4. 利回りの足切りは、どこで引くのが良いか")
    for lo in (0.0, 0.03, 0.04, 0.05):
        for hi in (0.99, 0.08, 0.065, 0.06):
            s = p[(p["start_yield"] >= lo) & (p["start_yield"] <= hi)]
            if len(s) < 100:
                continue
            print(f"    利回り {lo:.0%}〜{hi if hi<0.99 else 9.99:.0%}".ljust(24)
                  + f" n={len(s):>6,}  減配率 {s[target].mean():>5.1%}"
                    f"  リターン {s[f'ret_{h}y'].median():>+6.1%}")


if __name__ == "__main__":
    main()
