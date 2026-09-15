"""資金投入 — 「利益を移した。どこに入れるか」に答える。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.allocator import allocate, rebalance_funds
from modules.format import to_pct, yen, yen_short
from modules.portfolio import review_candidates, sector_exposure
from modules.store import read_df
from modules.ui import get_config, get_positions, get_scores, no_data_guard

st.title("💰 資金投入")
st.caption("入金額を、スコアと業種の空き枠に合わせて割り振る")

config = get_config()
scores = get_scores()
if no_data_guard(scores):
    st.stop()

positions = get_positions(config)
review = review_candidates(positions, config)

c1, c2, c3, c4 = st.columns(4)
with c1:
    cash = st.number_input("入金額（円）", 0, 100_000_000, 500_000, 50_000, format="%d")
with c2:
    max_names = st.number_input("買う銘柄数の上限", 1, 20, 5,
                                help="分散させすぎると監視が回らなくなる")
with c3:
    per_cap = st.slider("1銘柄あたりの上限（入金額に対する比率）", 10, 100, 30, 5,
                        format="%d%%") / 100
with c4:
    scope = st.radio("対象", ["未保有のみ", "保有の買い増しも含む"], index=1)
    # インカム目的の口座なので、利回りの下限は必須。これが無いと
    # スコアは高いが利回り1.2% といった銘柄に資金が入ってしまう。
    min_yield = st.number_input("最低利回り（%）", 0.0, 8.0, 3.0, 0.25,
                                help="これを下回る銘柄には資金を入れない") / 100

require_target = st.checkbox(
    "目標利回りに届いている銘柄だけに絞る", value=False,
    help="ウォッチリスト・保有に設定した target_yield を下回る株価まで待つ、という考え方")

sell_funds = rebalance_funds(review)
if sell_funds > 0:
    use_sell = st.checkbox(
        f"整理候補 {len(review)} 銘柄（{yen_short(sell_funds)}）を売却した資金も充てる",
        value=False)
    if use_sell:
        cash += sell_funds

held = set(positions["ticker"]) if not positions.empty else set()
cand = scores[scores["gate_passed"] == 1].copy()

detail = cand["detail_json"].map(
    lambda s: pd.read_json(s, typ="series")["raw"] if isinstance(s, str) else {})
raw = pd.DataFrame(list(detail)).set_index(cand.index)
cand = pd.concat([cand, raw[["dividend_yield", "yield_percentile", "streak"]]], axis=1)

if scope == "未保有のみ":
    cand = cand[~cand["ticker"].isin(held)]
cand = cand[cand["dividend_yield"].fillna(0) >= min_yield]

targets = read_df("SELECT ticker, target_yield FROM watchlist "
                  "UNION SELECT ticker, target_yield FROM holdings").groupby("ticker").first()
cand = cand.join(targets, on="ticker")

plan = allocate(cash, cand, positions, config, max_names=int(max_names),
                per_name_cap_pct=float(per_cap), require_below_target=require_target)

st.divider()
if plan.empty:
    st.warning("条件に合う配分先がありません。銘柄数の上限を増やすか、利回り条件を緩めてください。")
else:
    total_in = plan["投入額"].sum()
    total_div = plan["年間配当"].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("投入額", yen(total_in))
    c2.metric("増える年間配当（税引前）", yen(total_div))
    c3.metric("残り", yen(cash - total_in))

    show = plan[["コード", "銘柄名", "業種", "株価", "株数", "投入額", "利回り",
                 "年間配当", "スコア", "自己利回り順位", "連続増配", "業種の空き枠"]].copy()
    show["利回り"] = to_pct(show["利回り"])
    show["自己利回り順位"] = to_pct(show["自己利回り順位"])
    show["業種の空き枠"] = (show["業種の空き枠"] / 1e4).round(0)
    st.dataframe(
        show, hide_index=True, width="stretch",
        column_config={
            "株価": st.column_config.NumberColumn(format="¥%d"),
            "投入額": st.column_config.NumberColumn(format="¥%d"),
            "年間配当": st.column_config.NumberColumn(format="¥%d"),
            "利回り": st.column_config.NumberColumn(format="%.2f%%"),
            "スコア": st.column_config.NumberColumn(format="%.0f"),
            "自己利回り順位": st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100,
                help="その銘柄自身の過去7年の利回り分布の中での位置"),
            "連続増配": st.column_config.NumberColumn(format="%d 年"),
            "業種の空き枠": st.column_config.NumberColumn(
                format="%d 万円", help="この業種にあと何円まで入れられるか（構成比20%が上限）"),
        })
    st.caption("株数は単元（100株）に丸めています。"
               "スコアが高い順に、業種の上限と1銘柄あたりの上限を守りながら埋めています。")

st.divider()
st.subheader("整理を検討する保有")
if review.empty:
    st.success("基準を外れた保有はありません")
else:
    st.caption("売るかどうかはケンが決める。ここは材料を並べるだけ。")
    rev = review[["code", "name", "sector33", "account", "shares", "eval_value",
                  "pnl_pct", "total", "整理を検討する理由"]].rename(columns={
        "code": "コード", "name": "銘柄名", "sector33": "業種", "account": "口座",
        "shares": "株数", "eval_value": "評価額", "pnl_pct": "損益率", "total": "スコア"}).copy()
    rev["損益率"] = to_pct(rev["損益率"])
    rev["口座"] = rev["口座"].map({"specific": "特定", "nisa": "NISA"}).fillna(rev["口座"])
    st.dataframe(
        rev, hide_index=True, width="stretch", height=400,
        column_config={
            "評価額": st.column_config.NumberColumn(format="¥%d"),
            "損益率": st.column_config.NumberColumn(format="%.1f%%"),
            "スコア": st.column_config.NumberColumn(format="%.0f"),
        })

st.divider()
st.subheader("業種の空き枠")
exposure = sector_exposure(positions, config)
if not exposure.empty:
    exposure = exposure.rename(columns={"sector33": "業種"}).copy()
    exposure["構成比"] = to_pct(exposure["構成比"])
    st.dataframe(exposure, hide_index=True, width="stretch", height=400,
                 column_config={
                     "評価額": st.column_config.NumberColumn(format="¥%d"),
                     "年間配当": st.column_config.NumberColumn(format="¥%d"),
                     "構成比": st.column_config.NumberColumn(format="%.1f%%"),
                     "上限までの余裕": st.column_config.NumberColumn(format="¥%d"),
                 })
