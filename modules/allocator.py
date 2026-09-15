"""資金投入の配分エンジン。

「トレードで出た利益を移した。どこに入れるか」に答えるのが仕事。
スコアが高い順に上から買うだけだと業種が偏るので、33業種の上限を制約にした
貪欲法で埋めていく。単元株（原則100株）の刻みも考慮しないと現実に発注できない。

最適化ソルバは使わない。制約が少なく、上位候補から順に「入れられるか」を
判定していくだけで十分な精度が出るうえ、なぜその配分になったかを1行ずつ
説明できる。説明できない配分は使えない。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

_LOT = 100  # 東証の売買単位（2018年以降は原則100株）


def _lot_size(price: float, budget: float) -> int:
    if not price or price <= 0:
        return 0
    return int(budget // (price * _LOT)) * _LOT


def allocate(cash: float, candidates: pd.DataFrame, positions: pd.DataFrame,
             config: dict, max_names: int = 8,
             per_name_cap_pct: float = 0.25,
             require_below_target: bool = True) -> pd.DataFrame:
    """入金額を候補に割り振る。

    Parameters
    ----------
    cash : float
        今回投入する金額
    candidates : DataFrame
        発掘の結果。ticker/name/sector33/total/last_close/dividend_yield/
        target_yield（あれば）を持つ
    positions : DataFrame
        現在の保有（modules.portfolio.load_positions の出力）
    max_names : int
        1回の投入で買う銘柄数の上限。分散させすぎると監視が回らない
    per_name_cap_pct : float
        1銘柄あたりの投入上限（今回の入金額に対する比率）
    require_below_target : bool
        目標利回りに届いている銘柄だけを対象にする
    """
    if candidates is None or candidates.empty or cash <= 0:
        return pd.DataFrame()

    cap_sector = config["portfolio"]["max_sector_weight"]
    total_after = (positions["eval_value"].sum() if not positions.empty else 0.0) + cash
    sector_now = (positions.groupby("sector33")["eval_value"].sum()
                  if not positions.empty else pd.Series(dtype=float))

    c = candidates.copy()
    c = c[c["last_close"].fillna(0) > 0]
    if require_below_target and "target_yield" in c.columns:
        # 目標利回りに届いている＝いま買っていい水準、という判定
        c = c[c["dividend_yield"].fillna(0) >= c["target_yield"].fillna(0)]
    c = c.sort_values("total", ascending=False)

    per_name_cap = cash * per_name_cap_pct
    remaining = cash
    picks = []

    for _, r in c.iterrows():
        if len(picks) >= max_names or remaining < r["last_close"] * _LOT:
            continue
        sector = r.get("sector33")
        used = float(sector_now.get(sector, 0.0)) + sum(
            p["投入額"] for p in picks if p["業種"] == sector)
        room = cap_sector * total_after - used
        if room <= 0:
            continue
        budget = min(per_name_cap, remaining, room)
        shares = _lot_size(r["last_close"], budget)
        if shares < _LOT:
            continue
        amount = shares * r["last_close"]
        picks.append({
            "ticker": r["ticker"],
            "コード": r.get("code"),
            "銘柄名": r.get("name"),
            "業種": sector,
            "株価": r["last_close"],
            "株数": shares,
            "投入額": amount,
            "利回り": r.get("dividend_yield"),
            "年間配当": amount * (r.get("dividend_yield") or 0),
            "スコア": r.get("total"),
            "理由": _reason(r, sector, used, cap_sector * total_after),
        })
        remaining -= amount

    out = pd.DataFrame(picks)
    if not out.empty:
        out.attrs["残り"] = remaining
    return out


def _reason(r: pd.Series, sector, used: float, cap: float) -> str:
    bits = [f"スコア {r.get('total', float('nan')):.0f}"]
    yp = r.get("yield_percentile")
    if pd.notna(yp):
        bits.append(f"自己利回り上位 {yp:.0%}")
    streak = r.get("streak")
    if pd.notna(streak) and streak:
        bits.append(f"連続増配 {int(streak)}年")
    bits.append(f"{sector} の枠に余裕 {(cap - used) / 1e4:,.0f}万円")
    return " / ".join(bits)


def rebalance_funds(review: pd.DataFrame) -> float:
    """整理候補を売却した場合に作れる資金。"""
    if review is None or review.empty:
        return 0.0
    return float(review["eval_value"].sum())
