"""Streamlit 側の共通部品。ロジックは持たない。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.config import load_config
from modules.store import latest_scores, read_df

LAYER_LABELS = {
    "capacity": "増配余力",
    "willingness": "増配意思",
    "growth": "原資成長",
    "neglect": "見過ごされ度",
    "valuation": "割安",
}


@st.cache_data(ttl=600, show_spinner=False)
def get_config() -> dict:
    return load_config()


@st.cache_data(ttl=600, show_spinner="スコアを読み込み中...")
def get_scores() -> pd.DataFrame:
    return latest_scores()


@st.cache_data(ttl=600, show_spinner=False)
def get_holdings() -> pd.DataFrame:
    return read_df("SELECT * FROM holdings")


@st.cache_data(ttl=600, show_spinner="保有を集計中...")
def get_positions(_config: dict) -> pd.DataFrame:
    from modules.portfolio import load_positions
    return load_positions(_config)


@st.cache_data(ttl=600, show_spinner=False)
def get_watchlist() -> pd.DataFrame:
    return read_df("SELECT * FROM watchlist")


@st.cache_data(ttl=600, show_spinner=False)
def get_quotes() -> pd.DataFrame:
    return read_df("SELECT * FROM quotes")


@st.cache_data(ttl=600, show_spinner=False)
def get_dividend_profile(ticker: str) -> dict:
    from modules.dividend_history import build_profile
    div = read_df("SELECT date, amount FROM dividends WHERE ticker = ?", (ticker,))
    return build_profile(ticker, div).to_dict()


@st.cache_data(ttl=600, show_spinner=False)
def get_prices(ticker: str) -> pd.DataFrame:
    return read_df("SELECT date, close, volume FROM prices WHERE ticker = ? ORDER BY date",
                   (ticker,))


def no_data_guard(df: pd.DataFrame, what: str = "スコア") -> bool:
    """データが無いときに、次にやるべきコマンドまで含めて案内する。"""
    if df is not None and not df.empty:
        return False
    st.warning(
        f"{what}がまだありません。先に全市場スキャンを走らせてください。\n\n"
        "```bash\nuv run python scripts/weekly_scan.py\n```"
    )
    return True


def score_bar(row: pd.Series) -> None:
    """5層の内訳を横並びで見せる。総合点だけ見せてもブラックボックスになる。"""
    cols = st.columns(len(LAYER_LABELS) + 1)
    for col, (key, label) in zip(cols, LAYER_LABELS.items()):
        val = row.get(key)
        col.metric(label, f"{val:.0f}" if pd.notna(val) else "—")
    penalty = row.get("trap_penalty") or 0
    cols[-1].metric("トラップ減点", f"-{penalty:.0f}" if penalty else "なし")
