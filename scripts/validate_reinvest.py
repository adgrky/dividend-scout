"""検証16 — 受け取った配当を再投資すると、どれだけ違うのか。

【なぜやるか】
アプリには「配当を受け取ったら再投資に回す」導線を作った。だが**その効果を
数字で示したことが無い**。「複利だから効く」は誰でも言うが、日本の高配当株で
実際にいくら違うのかは測っていない。

【やり方】
検証14 と同じ積み立てを、配当の扱いだけ変えて回す。

    A 再投資する      … 受け取った配当を、その月の買い付けに足す
    B 現金で置いておく  … 受け取るが使わない（口座に貯まるだけ）
    C 使ってしまう     … 受け取った配当は生活費に消える（積み立ては入金だけ）

さらに、税引きの有無でも分ける（NISAか特定口座か）。

使い方:
    uv run python scripts/validate_reinvest.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                     # noqa: E402
import pandas as pd                    # noqa: E402

from modules import valreport as V     # noqa: E402
from modules.format import yen         # noqa: E402
from modules.sim import monthly_panel, yields   # noqa: E402

TAX = 0.20315


def simulate(panel: dict, y: pd.DataFrame, monthly: float, start: str,
             mode: str, tax: float, max_names: int = 200) -> dict:
    close, paid, cuts = panel["close"], panel["paid"], panel["cuts"]
    months = [m for m in close.index if m >= pd.Period(start, "M")]
    holdings: dict[str, float] = {}
    idle = 0.0                 # 使わずに置いてある現金
    income_total = 0.0
    invested = 0.0
    for m in months:
        px = close.loc[m]
        got = sum(sh * float(paid.at[m, t]) for t, sh in holdings.items()
                  if t in paid.columns and np.isfinite(paid.at[m, t]))
        got *= (1 - tax)
        income_total += got
        invested += monthly
        if mode == "A":
            cash = monthly + got
        elif mode == "B":
            cash = monthly
            idle += got
        else:                       # C 使ってしまう
            cash = monthly

        ok = y.loc[m].notna() & px.notna() & (px > 0)
        cand = y.loc[m][ok]
        c10 = cuts.loc[m].reindex(cand.index)
        cand = cand[c10.fillna(99) == 0]
        if cand.empty:
            continue
        held = set(holdings)
        pool = cand.drop(labels=[t for t in held if t in cand.index], errors="ignore")
        if pool.empty or len(held) >= max_names:
            pool = cand[[t for t in held if t in cand.index]]
        if pool.empty:
            continue
        pick = str(pool.idxmax())
        holdings[pick] = holdings.get(pick, 0.0) + cash / float(px[pick])

    px_last = close.loc[months[-1]]
    val = sum(sh * float(px_last.get(t, np.nan)) for t, sh in holdings.items()
              if t in px_last.index and np.isfinite(px_last.get(t, np.nan)))
    last12 = paid.loc[months[-12:]]
    ann = sum(sh * float(last12[t].sum()) for t, sh in holdings.items()
              if t in last12.columns) * (1 - tax)
    return {"株の評価額": val, "置いてある現金": idle, "合計": val + idle,
            "受け取った配当の累計": income_total, "いまの年間配当": ann,
            "投入した現金": invested}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monthly", type=float, default=100_000)
    a = ap.parse_args()
    panel = monthly_panel()
    y = yields(panel)
    print("検証16 — 受け取った配当を再投資すると、どれだけ違うのか")
    print(f"  毎月 {yen(a.monthly)} を積み立て。銘柄の選び方は「新規優先・利回りが高い順・減配歴なし」で固定")

    for start in ("2005-01", "2011-01"):
        n_years = (pd.Period("2026-09", "M") - pd.Period(start, "M")).n / 12
        V.hr(f"{start} から {n_years:.0f}年")
        for tax_name, tax in (("NISA（非課税）", 0.0), ("特定口座（20.315%）", TAX)):
            print(f"\n  【{tax_name}】")
            base = None
            for mode, label in (("A", "配当を再投資する"), ("B", "受け取って置いておく"),
                                ("C", "受け取って使ってしまう")):
                r = simulate(panel, y, a.monthly, start, mode, tax)
                if base is None:
                    base = r["合計"]
                print(f"    {label:<22s} 合計 {yen(r['合計']):>14s}"
                      f"（投入 {yen(r['投入した現金'])}）"
                      f"  いまの年間配当 {yen(r['いまの年間配当']):>10s}"
                      f"  {'' if mode == 'A' else f'再投資との差 {r[chr(0x5408)+chr(0x8a08)] / base - 1:>+6.1%}'}")

    V.hr("税の重さだけを取り出す")
    for start in ("2005-01", "2011-01"):
        a0 = simulate(panel, y, a.monthly, start, "A", 0.0)
        a1 = simulate(panel, y, a.monthly, start, "A", TAX)
        print(f"  {start} から  NISA {yen(a0['合計'])} 対 特定口座 {yen(a1['合計'])}"
              f"  → 差 {a1['合計'] / a0['合計'] - 1:+.1%}（{yen(a0['合計'] - a1['合計'])}）")
    print("\n  ※ 同じ銘柄・同じ買い方で、配当への課税だけが違う場合の差。")


if __name__ == "__main__":
    main()
