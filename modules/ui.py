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


@st.cache_data(ttl=3600, show_spinner="権利落ち日を計算中...")
def get_next_ex_dates(tickers: tuple[str, ...] | None = None) -> pd.DataFrame:
    """次の権利落ち日（配当履歴からの予測）。"""
    from modules.dividend_history import next_ex_dates
    return next_ex_dates(list(tickers) if tickers else None)


# ── 書き込みのあとに出すお知らせ ──────────────────────────────
# 記録したあと st.rerun() で画面を作り直すと、直前の st.success が消える。
# 逆に rerun しないと、書き込み前に計算した表がそのまま残って古い数字が見える。
# メッセージだけ持ち越して、作り直したあとに出す。
def flash(message: str, kind: str = "success") -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    got = st.session_state.pop("_flash", None)
    if got:
        kind, message = got
        getattr(st, kind, st.info)(message)


# ── データがいつ時点のものか ──────────────────────────────
# 1か月ぶりに開いても画面は今日と同じ顔をする。利回りも割安度も指値も評価額も
# ぜんぶ古い株価で計算されているのに、それが分からない。
# **お金の判断に使う画面なので、いつ時点の数字かは常に見えていなければならない。**
_STALE_DAYS = 8          # 週1回のスキャンを前提に、これを超えたら警告する
_VERY_STALE_DAYS = 21


@st.cache_data(ttl=300, show_spinner=False)
def last_update_failed() -> str:
    """直前の自動更新が失敗していたら、その内容を返す。

    自動更新は裏で走るので、失敗しても気づけない。気づかないまま古い数字で
    売買を決めるのがいちばん怖いので、画面に出す。
    """
    try:
        r = read_df("SELECT kind, finished_at, note FROM scan_runs "
                    "WHERE kind LIKE 'scheduled%' ORDER BY id DESC LIMIT 1")
    except Exception:
        return ""
    if r.empty or r["kind"].iloc[0] != "scheduled_failed":
        return ""
    return f"{str(r['finished_at'].iloc[0])[:16]}　{r['note'].iloc[0]}"


@st.cache_data(ttl=300, show_spinner=False)
def data_asof() -> dict:
    """株価・スコア・相場それぞれの「いつ時点か」。"""
    from datetime import date as _d
    out = {}
    for key, sql in (
        ("株価", "SELECT MAX(asof) v FROM quotes"),
        ("スコア", "SELECT MAX(asof) v FROM scores"),
        ("相場", "SELECT MAX(date) v FROM market_history"),
        ("配当", "SELECT MAX(date) v FROM dividends"),
    ):
        try:
            v = read_df(sql)["v"].iloc[0]
        except Exception:
            v = None
        if v:
            try:
                out[key] = pd.to_datetime(v).date()
            except Exception:
                pass
    if out:
        oldest = min(out.values())
        out["_古さ"] = (_d.today() - oldest).days
        out["_最古"] = oldest
    return out


def freshness_banner(sidebar: bool = True) -> None:
    """データの古さを出す。古ければ、やるべきコマンドまで書く。"""
    a = data_asof()
    if not a:
        return
    where0 = st.sidebar if sidebar else st
    # 更新スクリプトを走らせてもアプリ側は最大10分キャッシュを持つ。
    # 手で入れ替えられるようにしておかないと「更新したのに数字が変わらない」になる。
    if where0.button("🔄 読み込み直す", width="stretch",
                     help="データを更新したあとに押すと、新しい数字に入れ替わります"):
        st.cache_data.clear()
        st.rerun()
    days = a.get("_古さ", 0)
    where = st.sidebar if sidebar else st
    ng = last_update_failed()
    if ng:
        where.error(f"⚠️ **前回の自動更新が失敗しています**\n\n{ng}\n\n"
                    "**更新.command をダブルクリック**して手で更新してください。")
    detail = "　／　".join(f"{k} {v:%m/%d}" for k, v in a.items() if not k.startswith("_"))
    if days >= _VERY_STALE_DAYS:
        where.error(f"⚠️ **データが {days} 日前のものです**\n\n{detail}\n\n"
                    "この数字で売買を決めないでください。\n\n"
                    "**更新.command をダブルクリック**してください（約10分）。")
    elif days >= _STALE_DAYS:
        where.warning(f"データは **{days} 日前**（{a['_最古']:%Y-%m-%d}）\n\n{detail}\n\n"
                      "そろそろ更新どきです。**更新.command をダブルクリック**（約10分）。")
    else:
        where.caption(f"📅 データは **{a['_最古']:%Y-%m-%d}** 時点"
                      + ("（今日）" if days == 0 else f"（{days}日前）")
                      + f"\n\n{detail}")
