"""検証14 — 買い増し（ナンピン）と新規銘柄、どちらに入れるべきか。

【なぜやるか】
毎月の資金をどこに入れるかは、このアプリがいちばん多く下す判断。
アプリには「補完度」という考え方（持っていない業種・配当月を埋めると加点）が
入っているが、**買い増しそのものの是非は一度も測っていない**。

別のアプリ（stock-recommender）では「買い増しは全期間・全コスト帯で買い切りに
負けた」という結果が出ている。ただしあれはスイングトレードの話で、時間軸も
狙いも違う。配当株の積み立てでは別の答えになりうる。

【やり方】
2005年1月から毎月10万円を、実際の株価・配当で積み立てる。買う対象の決め方だけを
変えて、20年後の姿を比べる。

    A 新規優先       … まだ持っていない銘柄の中から、いちばん利回りが高いものを買う
    B 買い増し優先    … いま持っている銘柄の中から、いちばん利回りが高いものに足す
    C 区別しない      … 持っているかどうかを見ずに、いちばん利回りが高いものを買う
    D 下がったものに足す … 持っている中で、買値からいちばん下がっているものに足す（純ナンピン）
    E 新規優先・上限あり … A と同じだが、1銘柄が全体の一定割合を超えたら買わない

どれも「減配歴が無い」ことを条件にする（本検証で唯一効いたゲート）。

【見るもの】
    20年後の評価額 ／ 受け取った配当の累計 ／ 銘柄数 ／ 配当の集中度
    いちばん深い評価額の落ち込み

**リターンだけで決めない。** このアプリの目的はインカムなので、
受取配当と、その配当が何銘柄から出ているかを併せて見る。

使い方:
    uv run python scripts/validate_addon.py
    uv run python scripts/validate_addon.py --monthly 100000 --start 2005-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                     # noqa: E402
import pandas as pd                    # noqa: E402

from modules import valreport as V     # noqa: E402
from modules.sim import monthly_panel, yields   # noqa: E402

MAX_NAMES = 200


def simulate(panel: dict, y: pd.DataFrame, rule: str, monthly: float,
             start: str, cap: float = 1.0) -> dict:
    close, paid, cuts = panel["close"], panel["paid"], panel["cuts"]
    months = [m for m in close.index if m >= pd.Period(start, "M")]
    holdings: dict[str, float] = {}      # ticker -> 株数（小数。単元未満株を想定）
    cost: dict[str, float] = {}          # ticker -> 取得額
    income_total = 0.0
    invested = 0.0
    series, income_series = [], []

    for m in months:
        px = close.loc[m]
        # 1) 配当を受け取る（税は口座によるのでここでは引かない）
        got = 0.0
        for t, sh in holdings.items():
            amt = paid.at[m, t] if t in paid.columns else 0.0
            if amt and np.isfinite(amt):
                got += sh * float(amt)
        income_total += got
        cash = monthly + got          # 受け取った配当も同じ月に回す
        invested += monthly

        # 2) 候補を決める（その月までの情報だけ）
        ok = y.loc[m].notna() & px.notna() & (px > 0)
        cand = y.loc[m][ok]
        c10 = cuts.loc[m].reindex(cand.index)
        cand = cand[(c10.fillna(99) == 0)]
        if cand.empty:
            series.append(_value(holdings, px) + 0.0)
            income_series.append(income_total)
            continue

        held = set(holdings)
        if rule == "A":
            pool = cand.drop(labels=[t for t in held if t in cand.index], errors="ignore")
            if pool.empty or len(held) >= MAX_NAMES:
                pool = cand[[t for t in held if t in cand.index]]
        elif rule == "B":
            pool = cand[[t for t in held if t in cand.index]]
            if pool.empty:
                pool = cand
        elif rule == "C":
            pool = cand
        elif rule == "D":
            owned = [t for t in held if t in cand.index and t in cost]
            if not owned:
                pool = cand
            else:
                # 買値からの下落率が大きい順
                drop = pd.Series({t: (holdings[t] * float(px[t])) / cost[t] - 1
                                  for t in owned})
                pool = -drop
        elif rule in ("E", "F"):
            total = _value(holdings, px)
            over = {t for t in held
                    if total > 0 and holdings[t] * float(px.get(t, 0)) / total > cap}
            if rule == "E":
                # 新規優先。持っている銘柄に足すのは、候補が尽きたときだけ
                pool = cand.drop(labels=[t for t in held if t in cand.index],
                                 errors="ignore")
                if pool.empty or len(held) >= MAX_NAMES:
                    pool = cand[[t for t in held if t in cand.index and t not in over]]
            else:
                # 持っているかどうかは見ない。ただし上限を超えた銘柄だけ外す
                pool = cand.drop(labels=[t for t in over if t in cand.index],
                                 errors="ignore")
            if pool.empty:
                pool = cand
        else:
            raise ValueError(rule)

        if pool.empty:
            series.append(_value(holdings, px))
            income_series.append(income_total)
            continue
        pick = str(pool.idxmax())
        p = float(px[pick])
        sh = cash / p
        holdings[pick] = holdings.get(pick, 0.0) + sh
        cost[pick] = cost.get(pick, 0.0) + cash

        series.append(_value(holdings, px))
        income_series.append(income_total)

    px_last = close.loc[months[-1]]
    val = _value(holdings, px_last)
    # 直近12ヶ月の受取配当（いまの年間インカム）
    last12 = paid.loc[months[-12:]]
    ann = sum(sh * float(last12[t].sum()) for t, sh in holdings.items()
              if t in last12.columns)
    w = np.array([sh * float(last12[t].sum()) for t, sh in holdings.items()
                  if t in last12.columns], dtype=float)
    w = w[w > 0]
    eff = float(1 / np.sum((w / w.sum()) ** 2)) if len(w) else 0.0
    s = pd.Series(series, index=months)
    return {"最終評価額": val, "受け取った配当の累計": income_total,
            "いまの年間配当": ann, "投入した現金": invested,
            "銘柄数": len(holdings), "実質の分散銘柄数": eff,
            "落ち込み": float((1 - s / s.cummax()).max())}


def _value(holdings: dict, px: pd.Series) -> float:
    return float(sum(sh * float(px.get(t, np.nan)) for t, sh in holdings.items()
                     if t in px.index and np.isfinite(px.get(t, np.nan))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monthly", type=float, default=100_000)
    ap.add_argument("--start", default="2005-01")
    a = ap.parse_args()

    print("検証14 — 買い増しと新規、どちらに入れるべきか")
    panel = monthly_panel()
    y = yields(panel)
    print(f"  月次の表: {panel['close'].shape[0]} ヶ月 × {panel['close'].shape[1]:,} 銘柄")
    print(f"  毎月 {a.monthly:,.0f}円 を {a.start} から積み立て（受け取った配当も同じ月に回す）")

    rules = {
        "A 新規優先（持っていない銘柄から選ぶ）": ("A", 1.0),
        "B 買い増し優先（持っている銘柄に足す）": ("B", 1.0),
        "C 区別しない（いちばん利回りが高いものを買う）": ("C", 1.0),
        "D 下がったものに足す（純ナンピン）": ("D", 1.0),
        "E 新規優先・1銘柄5%まで": ("E", 0.05),
        "F 区別しない・1銘柄5%まで": ("F", 0.05),
        "G 区別しない・1銘柄3%まで": ("F", 0.03),
    }
    V.hr(f"1. {a.start} から積み立てた結果")
    rows = {}
    for label, (rule, cap) in rules.items():
        rows[label] = simulate(panel, y, rule, a.monthly, a.start, cap)
    t = pd.DataFrame(rows).T
    for c in ("最終評価額", "受け取った配当の累計", "いまの年間配当", "投入した現金"):
        t[c] = t[c].round(0)
    print(f"  {'やり方':<38s}{'最終評価額':>14s}{'受取配当の累計':>14s}"
          f"{'いまの年間配当':>14s}{'銘柄数':>7s}{'実質の分散':>10s}{'落ち込み':>9s}")
    for k, r in t.iterrows():
        print(f"  {k:<38s}{r['最終評価額']:>14,.0f}{r['受け取った配当の累計']:>14,.0f}"
              f"{r['いまの年間配当']:>14,.0f}{int(r['銘柄数']):>7d}"
              f"{r['実質の分散銘柄数']:>10.1f}{r['落ち込み']:>9.1%}")
    inv = t["投入した現金"].iloc[0]
    print(f"\n  投入した現金 {inv:,.0f}円（配当の再投資ぶんを除く）")

    V.hr("2. 始める時期を変えても同じか")
    print(f"  {'開始':<10s}" + "".join(f"{k.split()[0]:>12s}" for k in rules))
    for st in ("2005-01", "2008-01", "2011-01", "2014-01", "2017-01"):
        vals = []
        for label, (rule, cap) in rules.items():
            r = simulate(panel, y, rule, a.monthly, st, cap)
            vals.append(r["最終評価額"] / (r["投入した現金"] or 1))
        print(f"  {st:<10s}" + "".join(f"{v:>12.2f}" for v in vals))
    print("\n  数字は「投入した現金の何倍になったか」。")


if __name__ == "__main__":
    main()
