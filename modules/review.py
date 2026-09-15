"""整理の判断を覚えておく。

保有114銘柄のうち84銘柄が「整理を検討」に出る。毎回そのまま並べていたら、
一度見て「持ち続ける」と決めた銘柄がまた並び、そのうち画面ごと見なくなる。
判断を記録して、次からは既定では隠す。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from modules.store import connect, read_df

DECISIONS = {
    "keep": "持ち続ける",
    "watch": "様子見",
    "sell": "売る予定",
}


def load() -> pd.DataFrame:
    return read_df("SELECT * FROM holding_review")


def save(account: str, ticker: str, decision: str, note: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO holding_review (account, ticker, decision, decided_at, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (account, ticker, decision, datetime.now().strftime("%Y-%m-%d"), note))


def clear(account: str, ticker: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM holding_review WHERE account=? AND ticker=?",
                     (account, ticker))


def attach(review: pd.DataFrame) -> pd.DataFrame:
    """整理候補に、過去の判断を貼り付ける。"""
    if review is None or review.empty:
        return review
    dec = load()
    out = review.copy()
    if dec.empty:
        out["判断"] = ""
        out["判断日"] = ""
        return out
    key = dec.set_index(["account", "ticker"])
    idx = pd.MultiIndex.from_frame(out[["account", "ticker"]])
    out["判断"] = [DECISIONS.get(key["decision"].get(k), "") if k in key.index else ""
                 for k in idx]
    out["判断日"] = [key["decided_at"].get(k, "") if k in key.index else "" for k in idx]
    return out
def sell_priority(review: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    ※ 使っていない。整理の優先度は modules/sell_rules.evaluate が出す。
どれから整理すべきかの順位。

    「配当が危ない」ほど、そして「金額が大きい」ほど先に手をつける価値がある。
    含み損の銘柄を先に売れば、特定口座では譲渡益と相殺できて税金が軽くなる。
    """
    if review is None or review.empty:
        return review
    out = review.copy()
    danger = (100 - out["health"].fillna(50)).clip(0, 100)
    size = out["eval_value"].rank(pct=True) * 100
    # 含み損は「売っても税金がかからない／他の利益と相殺できる」ぶん動かしやすい
    tax_ease = (-out["pnl_pct"].fillna(0) * 100).clip(-50, 50) + 50
    out["整理の優先度"] = (0.5 * danger + 0.3 * size + 0.2 * tax_ease).round(0)
    return out.sort_values("整理の優先度", ascending=False)
def capital_gain_tax(pos_row: pd.Series, shares: float, price: float, config: dict) -> float:
    """売却時にかかる税金の見込み（特定口座のみ。NISAは非課税）。"""
    if pos_row.get("account") == "nisa":
        return 0.0
    cost = float(pos_row.get("avg_cost") or 0) * shares
    gain = shares * price - cost
    if gain <= 0:
        return 0.0
    return gain * float(config["portfolio"]["tax_rate_specific"])
