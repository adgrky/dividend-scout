"""C層（原資成長）の検証 — 配当の元手が伸びている会社は、報われるのか。

【なぜ今まで測れなかったか】
yfinance の財務は4〜5期しか返らないので、2015年時点の営業CFや利益率を
再現できなかった。検証レポートの「6. 検証できていないこと」に A層と並んで
C層が載っているのはそのため。過去の有報を遡って取り込んだので、いま測れる。

【見るもの】
    EPS 3年成長・営業CF 3年成長・売上 3年成長
    ROE・経常利益率・経常利益率の3年変化
    増益の質 = 営業CFの伸び − 純利益の伸び
        （利益だけ伸びて現金が伸びないのは、会計上の見せかけを疑う）

【判断日】各年度の期末から3ヶ月後。その日までに出ている数字しか使わない。
【答え合わせ】5年後のトータルリターン・DPS成長・減配の発生。
【学習と検証】前半のコホートで見て、後半で答え合わせする。

使い方:
    uv run python scripts/validate_growth.py
    uv run python scripts/validate_growth.py --fy 2014,2016,2018,2020 --horizon 5
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
    "C_EPS 3年成長": "g3_eps",
    "C_営業CF 3年成長": "g3_ocf",
    "C_売上 3年成長": "g3_sales",
    "C_ROE": "roe",
    "C_経常利益率": "margin",
    "C_経常利益率の3年変化": "d3_margin",
    "C_増益の質（CF−利益）": "quality",
    "（比較）起点の利回り": "start_yield",
}


def add_features(p: pd.DataFrame, lag: int = 3) -> pd.DataFrame:
    """3年前と比べる。年度が飛んでいる銘柄では作らない。"""
    p = p.sort_values(["ticker", "fiscal_year"]).copy()
    g = p.groupby("ticker", sort=False)
    ok = (p["fiscal_year"] - g["fiscal_year"].shift(lag)) == lag
    for src, name in (("eps_adj", "eps"), ("operating_cf", "ocf"),
                      ("sales", "sales"), ("net_income", "ni")):
        prev = g[src].shift(lag).where(ok)
        with np.errstate(divide="ignore", invalid="ignore"):
            # 赤字から黒字への「成長率」は意味を持たないので、元が正の年だけ
            p[f"g{lag}_{name}"] = np.where(prev > 0, p[src] / prev - 1, np.nan)
    p[f"d{lag}_margin"] = p["margin"] - g["margin"].shift(lag).where(ok)
    p["quality"] = p[f"g{lag}_ocf"] - p[f"g{lag}_ni"]
    for c in (f"g{lag}_eps", f"g{lag}_ocf", f"g{lag}_sales", "quality"):
        p[c] = p[c].clip(-2, 10)
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy", default="2014,2016,2018,2020")
    ap.add_argument("--horizon", type=int, default=5)
    a = ap.parse_args()
    fys = [int(x) for x in a.fy.split(",")]
    h = a.horizon
    out = {"DPS成長": f"dps_growth_{h}y", "リターン": f"ret_{h}y", "減配の発生": f"cut_{h}y"}

    print(f"C層（原資成長）の検証  起点 FY{fys} ／ 先 {h} 年")
    p = add_features(load_cached())
    p = p[p[f"ret_{h}y"].notna() & (p["start_yield"] > 0)]

    frames = {}
    for fy in fys:
        c = p[p["fiscal_year"] == fy]
        if len(c) < 100:
            print(f"  FY{fy}: {len(c)} 銘柄しかない。除外します")
            continue
        print(f"  FY{fy}（判断日 ≒ {fy+1}-06 → 実績 {fy+1+h}-06）: {len(c):,} 銘柄")
        frames[fy] = c
    if not frames:
        print("コホートが作れませんでした")
        return
    df = pd.concat(frames.values(), ignore_index=True)
    print(f"  合計 延べ {len(df):,} 銘柄 / {len(frames)} コホート")

    V.hr("1. 各指標と実績の順位相関（全コホート結合）")
    print(V.corr_table(df, FACTORS, out).round(3).to_string())
    print(f"\n目安: |相関| < {V.NOISE} はノイズ。{V.REAL} を超えたら意味がある可能性。")

    if len(frames) > 1:
        for on, oc in out.items():
            V.hr(f"2. コホート別 — {on}（時期をまたいで安定しているか）")
            t = V.cohort_table(frames, FACTORS, oc)
            t["判定"] = t.apply(V.verdict, axis=1)
            print(t.round(3).to_string())

    V.hr("3. 分位別の実績（5分位）")
    V.show_quintiles(df, FACTORS, out)

    V.hr("4. 高利回りに「原資が伸びている」を足すと良くなるか")
    for label, sub in [("全コホート", df)] + [(f"FY{k}", v) for k, v in frames.items()]:
        print(f"\n  【{label}】")
        hi = sub["start_yield"] >= sub["start_yield"].quantile(0.7)
        V.gate_effect(sub, hi, [
            ("  ＋ EPSが3年で増えている", sub["g3_eps"] > 0),
            ("  ＋ 営業CFも3年で増えている", sub["g3_ocf"] > 0),
            ("  ＋ 利益率が下がっていない", sub["d3_margin"] >= 0),
        ], out)

    if len(frames) >= 2:
        V.hr("5. 前半で見て、後半で答え合わせ")
        tr, te, ktr, kte = V.split_halves(frames)
        print(f"  学習 FY{ktr}（{len(tr):,}）／ 検証 FY{kte}（{len(te):,}）")
        for nm, sub in (("学習", tr), ("検証", te)):
            print(f"\n  【{nm}】")
            hi = sub["start_yield"] >= sub["start_yield"].quantile(0.7)
            V.gate_effect(sub, hi, [
                ("  ＋ EPSが3年で増えている", sub["g3_eps"] > 0),
                ("  ＋ 営業CFも3年で増えている", sub["g3_ocf"] > 0),
            ], out)
        print("\n  **後半でも同じ向きに動いたときだけ、足切りを既定にしてよい。**")


if __name__ == "__main__":
    main()
