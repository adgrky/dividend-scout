"""配当そのものの分散と、不況への耐性を測る。

【なぜ必要か】
2026-09-15 の検証で、いちばん重い数字が残った。
**最良の選び方（高利回り × 減配歴なし）でも、5年で 39.5% が減配する。**
（2026-09-16 に年度の切り方のバグを直して測り直した値。それ以前は 54.7% と出ていた）
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

from modules.dividend_history import annual_dps
from modules.config import load_config
from modules.store import read_df

# 実際に起きた不況。(名前, 平時の年, 傷んだ年の範囲, 説明)
#
# 「傷んだ年」を1年に決め打ちすると、会社ごとに減配の出る年がずれる（決算期が違う、
# 期末で減らす会社と翌期の中間で減らす会社がある）。実測でも、全上場の減配銘柄の
# 割合は リーマンで 2009年 41.0% → 2010年 30.7%、コロナで 2020年 21.4% →
# 2021年 26.3% と2年にまたがっていた。**範囲の中でいちばん低かった年**を取る。
EVENTS = [
    ("リーマン・ショック", 2007, (2009, 2010),
     "世界金融危機。全上場の41%が減配し、翌年もまだ31%が減らした"),
    ("コロナ・ショック", 2019, (2020, 2021),
     "需要が消えた業種と、そうでない業種の差が大きかった"),
    ("東日本大震災", 2010, (2011, 2012),
     "供給網の断絶。影響は業種によって偏った"),
]


def _worst_dps(div: pd.DataFrame, ticker: str, years: tuple[int, int]) -> float:
    """その期間のうち、いちばん配当が低かった年の1株配当。"""
    vals = [_fiscal_dps(div, ticker, y) for y in range(years[0], years[1] + 1)]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else 0.0


def _fiscal_dps(div: pd.DataFrame, ticker: str, year: int) -> float:
    """その年度の1株配当の合計。

    以前はここで「4月〜翌3月」と決め打ちしていた。だが3月期以外の会社では
    2つの年度をまたいでしまう（9月期の会社なら、9月の期末配当と翌3月の中間配当を
    足すことになる）。しかも年度の切り方が modules/dividend_history.annual_dps と
    違っていたので、同じ「2019年度の配当」が画面によって別の数字になりえた。

    年度の切り方はアプリの中で1つに統一する。annual_dps は各社の権利落ち日から
    決算月を割り出し、権利落ち日の数日のズレも吸収する。
    """
    g = div[div["ticker"] == ticker]
    if g.empty:
        return 0.0
    table = annual_dps(g[["date", "amount"]])
    if table.empty or year not in table.index:
        return 0.0
    return float(table.loc[year, "dps"])


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
            a = _worst_dps(div, t, after)
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
            "年度": f"{before} → {after[0]}〜{after[1]} の最低",
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
        a = _worst_dps(div, t, after)
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


def cyclical_exposure(pos: pd.DataFrame, config: dict | None = None) -> dict:
    """不況に弱い業種が、年間配当の何割を占めているか。

    **評価額ではなく配当で見る。** 高利回りの景気敏感株は、評価額に占める割合より
    配当に占める割合のほうが大きくなる。守りたいのはインカムなので、そちらで測る。

    どの業種が「弱い」かは config の cyclical.sectors。リーマンの実績だけで選び、
    コロナで答え合わせ済み（詳細は config.yaml のコメント）。
    """
    config = config or load_config()
    secs = set(config.get("cyclical", {}).get("sectors", []))
    cap = float(config.get("cyclical", {}).get("max_income_share", 0.25))
    if pos is None or pos.empty or not secs:
        return {}
    weak = pos["sector33"].isin(secs)
    inc = float(pos["annual_dividend"].sum()) or 1.0
    val = float(pos["eval_value"].sum()) or 1.0
    share = float(pos.loc[weak, "annual_dividend"].sum()) / inc
    detail = (pos[weak].groupby("sector33")["annual_dividend"].sum()
              .sort_values(ascending=False) / inc)
    return {
        "対象業種": sorted(secs),
        "銘柄数": int(weak.sum()),
        "評価額に占める割合": float(pos.loc[weak, "eval_value"].sum()) / val,
        "配当に占める割合": share,
        "上限": cap,
        "超過": max(0.0, share - cap),
        "超過している配当額": max(0.0, share - cap) * inc,
        "業種別": detail,
    }
