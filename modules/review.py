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