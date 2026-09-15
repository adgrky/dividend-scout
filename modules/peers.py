"""同業他社との横並び比較。

【なぜ必要か】
カルテには「配当性向 28.5%」「ROE 9.3%」といった実数が出るようになったが、
**その水準が業種として高いのか低いのかは、同業と比べないと分からない**。
営業利益率は業種で何倍も違うし、自己資本比率も設備産業と小売では意味が違う。

採用基準（ゲート）を通った約700社の中から、同じ33業種の会社を並べる。
ゲートを通っていない会社まで混ぜると、比較の母集団が「増配候補」でなくなる。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

# 比較する項目（列名, raw のキー, 高いほうが良いか, 表示形式）
METRICS: list[tuple[str, str, bool, str]] = [
    ("配当利回り", "dividend_yield", True, "pct"),
    ("配当性向", "payout_ratio", False, "pct"),
    ("連続増配", "streak", True, "year"),
    ("DPS 5年成長", "cagr_5y", True, "pct"),
    ("PER", "per", False, "x"),
    ("PBR", "pbr", False, "x"),
    ("ROE", "roe", True, "pct"),
    ("自己資本比率", "equity_ratio", True, "pct"),
    ("営業利益率", "operating_margin", True, "pct"),
    ("時価総額", "market_cap_oku", True, "oku"),
]


def build(scores: pd.DataFrame, sector: str, ticker: str,
          gate_only: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """同業の一覧と、この銘柄の業種内での位置を返す。

    Returns
    -------
    (一覧, 位置)
        一覧 … 同業各社の実数（この銘柄を含む）
        位置 … 指標ごとの「この銘柄の値 / 業種の中央値 / 業種内の順位」
    """
    df = scores[scores["sector33"] == sector].copy()
    if gate_only:
        df = df[df["gate_passed"] == 1]
    if df.empty or ticker not in set(df["ticker"]) and gate_only:
        # 本人がゲートを外れている場合は、本人だけ足して比較できるようにする
        me = scores[scores["ticker"] == ticker]
        df = pd.concat([df, me], ignore_index=True)
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    raw = pd.DataFrame([
        json.loads(x).get("raw", {}) if isinstance(x, str) else {}
        for x in df["detail_json"]
    ], index=df.index)
    for _, key, _, _ in METRICS:
        df[key] = pd.to_numeric(raw.get(key), errors="coerce")

    cols = ["ticker", "code", "name", "total", "health"] + [k for _, k, _, _ in METRICS]
    table = df[[c for c in cols if c in df.columns]].copy()

    rows = []
    for label, key, higher_better, fmt in METRICS:
        if key not in table.columns:
            continue
        series = table[key].dropna()
        if series.empty or ticker not in set(table["ticker"]):
            continue
        mine = table.loc[table["ticker"] == ticker, key]
        if mine.empty or pd.isna(mine.iloc[0]):
            continue
        val = float(mine.iloc[0])
        rank = int((series > val).sum()) + 1 if higher_better else int((series < val).sum()) + 1
        n = int(len(series))
        pctile = 1 - (rank - 1) / n if n > 1 else 0.5
        rows.append({
            "指標": label, "_key": key, "_fmt": fmt,
            "この銘柄": val,
            "業種の中央値": float(series.median()),
            "業種の最良": float(series.max() if higher_better else series.min()),
            "業種内の順位": f"{rank} / {n} 社",
            "_pctile": pctile,
            "評価": ("上位" if pctile >= 0.7 else "下位" if pctile <= 0.3 else "ふつう"),
        })
    return table, pd.DataFrame(rows)


def fmt(value, kind: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "x":
        return f"{value:.1f} 倍"
    if kind == "year":
        return f"{value:.0f} 年"
    if kind == "oku":
        return f"{value:,.0f} 億円"
    return f"{value:,.2f}"
