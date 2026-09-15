"""配当そのものの分散と、不況への耐性を測る。

【なぜ必要か】
2026-09-15 の検証で、いちばん重い数字が残った。
**最良の選び方（高利回り × 減配歴なし）でも、5年で 54.7% が減配する。**
選別でこれ以上下げられないことは、業種で下げようとして失敗したことで確かめた
（業種ごとの減配率は 46.6%〜82.0% と差があるのに、2014年と2020年の順位相関は 0.08）。

選別で防げないなら、**起きたときにどれだけ減るかを知っておく**しかない。

そしてもう一つ。このアプリの業種上限は **評価額** にかかっていて、**配当** には
かかっていない。ケンの保有114銘柄を実測すると、

    上位 5銘柄で 年間配当の 38%
    上位10銘柄で 年間配当の 52%

114銘柄に分散しているように見えて、**インカムは10銘柄から出ている**。
評価額の分散と、配当の分散は別物。ここを見るものが1つも無かった。
"""
from __future__ import annotations

import pandas as pd

from modules.store import read_df

# 実際に起きた不況。年度は 4月〜翌3月。
EVENTS = [
    ("リーマン・ショック", 2007, 2009,
     "世界金融危機。日本の配当は2009年度に最も落ちた"),
    ("コロナ・ショック", 2019, 2020,
     "需要が消えた業種と、そうでない業種の差が大きかった"),
    ("東日本大震災", 2010, 2011,
     "供給網の断絶。影響は業種によって偏った"),
]


def _fiscal_dps(div: pd.DataFrame, ticker: str, year: int) -> float:
    """その年度（4月〜翌3月）の1株配当の合計。"""
    g = div[(div["ticker"] == ticker)
            & (div["date"] >= pd.Timestamp(f"{year}-04-01"))
            & (div["date"] < pd.Timestamp(f"{year + 1}-04-01"))]
    return float(g["amount"].sum())


def concentration(pos: pd.DataFrame) -> dict:
    """配当が、どれだけ一部の銘柄から出ているか。

    「実質何銘柄ぶんの分散か」は 1 ÷ Σ(構成比²)。
    50銘柄あっても1銘柄に半分が寄っていれば、実質は数銘柄ぶんにしかならない。
    """
    if pos is None or pos.empty:
        return {}
    s = pos.groupby("ticker")["annual_dividend"].sum()
    s = s[s > 0].sort_values(ascending=False)
    total = float(s.sum())
    if total <= 0:
        return {}
    w = s / total
    return {
        "銘柄数": int(len(pos)),
        "配当が出ている銘柄数": int(len(s)),
        "年間配当": total,
        "上位5の割合": float(w.head(5).sum()),
        "上位10の割合": float(w.head(10).sum()),
        "上位20の割合": float(w.head(20).sum()),
        "実質の分散銘柄数": float(1.0 / (w ** 2).sum()),
        "内訳": s,
    }


def stress_test(pos: pd.DataFrame) -> pd.DataFrame:
    """保有銘柄が、過去の不況で実際に配当をどれだけ減らしたか。

    **当時まだ上場していなかった銘柄は分からない。** 分かるぶんだけで割合を出し、
    「残りも同じように振る舞ったら」という形で全体に当てはめる。
    分からないものを0として扱うと、被害を小さく見せてしまう。
    """
    if pos is None or pos.empty:
        return pd.DataFrame()
    tickers = sorted(set(pos["ticker"]))
    ph = ",".join("?" * len(tickers))
    div = read_df(f"SELECT ticker, date, amount FROM dividends WHERE ticker IN ({ph})",
                  tuple(tickers))
    if div.empty:
        return pd.DataFrame()
    div["date"] = pd.to_datetime(div["date"])
    now = pos.groupby("ticker")["annual_dividend"].sum()
    total_now = float(now.sum())

    rows = []
    for label, before, after, note in EVENTS:
        measured, lost, n_have, n_cut = 0.0, 0.0, 0, 0
        for t in tickers:
            b = _fiscal_dps(div, t, before)
            if b <= 0:
                continue                       # 当時まだ配当が無い＝分からない
            a = _fiscal_dps(div, t, after)
            w = float(now.get(t, 0.0))
            measured += w
            n_have += 1
            ratio = min(a / b, 1.0)
            if ratio < 0.999:
                n_cut += 1
                lost += w * (1 - ratio)
        if measured <= 0:
            continue
        rate = lost / measured
        rows.append({
            "できごと": label,
            "説明": note,
            "年度": f"{before} → {after}",
            "調べられた銘柄": n_have,
            "うち減配": n_cut,
            "調べられた配当の割合": measured / total_now if total_now else 0,
            "減った割合": rate,
            "いまの配当に当てはめた減少額": rate * total_now,
            "残る年間配当": total_now * (1 - rate),
        })
    return pd.DataFrame(rows)


def worst_contributors(pos: pd.DataFrame, event: str, top: int = 10) -> pd.DataFrame:
    """その不況で、配当をいちばん減らした銘柄。"""
    ev = next((e for e in EVENTS if e[0] == event), None)
    if ev is None or pos is None or pos.empty:
        return pd.DataFrame()
    _, before, after, _ = ev
    tickers = sorted(set(pos["ticker"]))
    ph = ",".join("?" * len(tickers))
    div = read_df(f"SELECT ticker, date, amount FROM dividends WHERE ticker IN ({ph})",
                  tuple(tickers))
    div["date"] = pd.to_datetime(div["date"])
    now = pos.groupby("ticker")["annual_dividend"].sum()
    names = pos.groupby("ticker")["name"].first()
    codes = pos.groupby("ticker")["code"].first()
    rows = []
    for t in tickers:
        b = _fiscal_dps(div, t, before)
        if b <= 0:
            continue
        a = _fiscal_dps(div, t, after)
        ratio = min(a / b, 1.0)
        if ratio >= 0.999:
            continue
        w = float(now.get(t, 0.0))
        rows.append({
            "コード": codes.get(t), "銘柄名": names.get(t),
            "当時の1株配当": b, "その後の1株配当": a,
            "減配率": ratio - 1,
            "いまの年間配当": w,
            "失う配当": w * (1 - ratio),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("失う配当", ascending=False).head(top)


def income_by_sector(pos: pd.DataFrame) -> pd.DataFrame:
    """業種ごとの「配当の」構成比。評価額の構成比とは別物。

    評価額で20%以内に収まっていても、配当が特定の業種に寄っていることがある。
    インカムを守るなら、見るべきはこちら。
    """
    if pos is None or pos.empty:
        return pd.DataFrame()
    g = pos.groupby("sector33", dropna=False).agg(
        銘柄数=("ticker", "count"),
        評価額=("eval_value", "sum"),
        年間配当=("annual_dividend", "sum")).reset_index()
    tot_v = g["評価額"].sum()
    tot_d = g["年間配当"].sum()
    g["評価額の構成比"] = g["評価額"] / tot_v if tot_v else 0
    g["配当の構成比"] = g["年間配当"] / tot_d if tot_d else 0
    g["差"] = g["配当の構成比"] - g["評価額の構成比"]
    return g.sort_values("配当の構成比", ascending=False)
