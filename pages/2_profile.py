"""銘柄カルテ — 1銘柄を1枚で判断できるようにする。

出すのは4つだけ。
    1. なぜ増配が続くと考えられるか（層ごとの数値）
    2. 市場が見落としている理由の仮説
    3. いくらで買えば目標利回りに届くか（指値）
    4. 棄却条件 — 何が起きたら間違いだったと認めるか
"""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.format import pct, yen, yen_short
from modules.store import read_df
from modules.ui import (LAYER_LABELS, get_config, get_dividend_profile, get_prices,
                        get_scores, no_data_guard, score_bar)
from modules.valuation import price_for_target_yield, yield_percentile, yield_series

st.title("📄 銘柄カルテ")

config = get_config()
scores = get_scores()
if no_data_guard(scores):
    st.stop()

scores = scores.sort_values("total", ascending=False)
labels = scores.apply(lambda r: f"{r['code']} {r['name']}（{r['sector33']}）", axis=1)
options = dict(zip(labels, scores["ticker"]))

pre = st.query_params.get("ticker")
default_idx = 0
if pre and pre in set(scores["ticker"]):
    default_idx = list(options.values()).index(pre)

choice = st.selectbox("銘柄", list(options.keys()), index=default_idx)
ticker = options[choice]
row = scores[scores["ticker"] == ticker].iloc[0]

prof = get_dividend_profile(ticker)
prices = get_prices(ticker)
div = read_df("SELECT date, amount FROM dividends WHERE ticker = ? ORDER BY date", (ticker,))
detail = json.loads(row["detail_json"]) if isinstance(row["detail_json"], str) else {}
raw = detail.get("raw", {})

# ── ヘッダー ──
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("総合スコア", f"{row['total']:.1f}" if pd.notna(row["total"]) else "—")
c2.metric("株価", yen(row.get("last_close")))
c3.metric("利回り（実績）", pct(raw.get("dividend_yield"), 2))
c4.metric("連続増配", f"{int(prof['streak'])} 年")
c5.metric("10年の減配", f"{int(prof['cuts_10y'])} 回")

if row["gate_passed"] != 1:
    st.error(f"**採用基準を外れている**：{row['gate_reason']}")

st.divider()

# ── 1. なぜ増配が続くと考えられるか ──
st.subheader("1. なぜ増配が続くと考えられるか")
score_bar(row)

cols = st.columns(len(LAYER_LABELS))
for col, (key, label) in zip(cols, LAYER_LABELS.items()):
    with col:
        st.markdown(f"**{label}**")
        parts = detail.get(key, {})
        if not parts:
            st.caption("—")
            continue
        for k, v in parts.items():
            st.caption(f"{k}　**{v:.0f}**" if isinstance(v, (int, float)) else f"{k}　—")

st.markdown("**根拠になっている実数**")
facts = pd.DataFrame([
    ("配当性向", pct(raw.get("payout_ratio"))),
    ("FCF配当カバー率", f"{raw.get('fcf_cover'):.1f} 倍" if raw.get("fcf_cover") else "—"),
    ("ネットキャッシュ比率", pct(raw.get("net_cash_ratio"))),
    ("ネット有利子負債 ÷ 営業CF", f"{raw.get('net_debt_to_ocf'):.1f} 年" if raw.get("net_debt_to_ocf") is not None else "—"),
    ("自己資本比率", pct(raw.get("equity_ratio"))),
    ("ROE", pct(raw.get("roe"))),
    ("DPS 5年成長", pct(raw.get("cagr_5y"))),
    ("DPS 10年成長", pct(raw.get("cagr_10y"))),
    ("純利益の伸び", pct(raw.get("ni_cagr"))),
    ("営業CFの伸び", pct(raw.get("ocf_cagr"))),
    ("時価総額", f"{raw.get('market_cap_oku'):,.0f} 億円" if raw.get("market_cap_oku") else "—"),
    ("PER / PBR", f"{raw.get('per', 0):.1f} / {raw.get('pbr', 0):.2f}"),
], columns=["項目", "値"])
st.dataframe(facts, hide_index=True, width="stretch", height=460)

st.divider()

# ── 配当の推移 ──
st.subheader("配当の推移")
series = pd.Series(prof["series"]).sort_index()
if not series.empty:
    fig = go.Figure()
    fig.add_bar(x=series.index.astype(str), y=series.values, name="DPS",
                marker_color="#4C8BF5")
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10),
                      yaxis_title="1株配当（円）", xaxis_title="配当年度")
    st.plotly_chart(fig, width="stretch")
    if prof["has_spike"]:
        st.caption("※ 記念配当・特別配当と思われる年を検出しています。"
                   "連続増配・減配回数の判定からは除外しています。")

# ── 3. 指値 ──
st.subheader("2. いくらで買えば目標利回りに届くか")
info = yield_percentile(prices, div, config["scoring"]["yield_percentile_window_years"])
hold = read_df("SELECT target_yield, bottom_yield FROM holdings WHERE ticker = ?", (ticker,))
watch = read_df("SELECT target_yield, bottom_yield FROM watchlist WHERE ticker = ?", (ticker,))
src = hold if not hold.empty else watch
default_target = float(src["target_yield"].iloc[0]) if not src.empty and pd.notna(src["target_yield"].iloc[0]) else 0.047

c1, c2 = st.columns([1, 2])
with c1:
    target = st.number_input("目標利回り（%）", 1.0, 12.0, default_target * 100, 0.1) / 100
    dps = prof["dps_latest"]
    limit_price = price_for_target_yield(dps, target)
    st.metric("指値", yen(limit_price))
    now_price = row.get("last_close")
    if limit_price and now_price:
        gap = limit_price / now_price - 1
        st.caption(f"現在値から {gap:+.1%}")
    if info.get("price_at_median_yield"):
        st.metric("過去中央値の利回りまで戻った場合の株価",
                  yen(info["price_at_median_yield"]))

with c2:
    ys = yield_series(prices, div)
    if not ys.empty:
        cutoff = ys.index.max() - pd.DateOffset(years=10)
        ys = ys[(ys.index >= cutoff) & (ys > 0)]
        fig = go.Figure()
        fig.add_scatter(x=ys.index, y=ys.values * 100, mode="lines",
                        name="利回り", line=dict(color="#4C8BF5"))
        if info.get("median"):
            fig.add_hline(y=info["median"] * 100, line_dash="dot",
                          annotation_text="過去中央値")
        fig.add_hline(y=target * 100, line_dash="dash", line_color="#E45756",
                      annotation_text="目標利回り")
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="配当利回り（%）")
        st.plotly_chart(fig, width="stretch")
    if info.get("percentile") is not None:
        st.caption(
            f"いまの利回りは、この銘柄の過去"
            f"{config['scoring']['yield_percentile_window_years']}年の分布の "
            f"**上位 {info['percentile']:.0%}**（100%に近いほど自分史上まれに見る高利回り）。"
            f"過去の中央値 {pct(info.get('median'), 2)} / 四分位 "
            f"{pct(info.get('p25'), 2)}〜{pct(info.get('p75'), 2)}"
        )

st.divider()

# ── 4. 棄却条件 ──
st.subheader("3. 何が起きたら間違いだったと認めるか")
traps_found = detail.get("traps") or []
if traps_found:
    for t in traps_found:
        st.warning(t)

auto = []
payout = raw.get("payout_ratio")
if payout:
    auto.append(f"配当性向が {min(payout + 0.20, 0.90):.0%} を超えたら、増配の原資が尽きかけている")
auto.append("純利益または営業CFが2期連続で減少したら、増配ストーリーの前提が崩れる")
auto.append("減配または無配転落が発表されたら、その時点で仮説は棄却")
if raw.get("net_debt_to_ocf") is not None:
    auto.append(f"ネット有利子負債 ÷ 営業CF が {raw['net_debt_to_ocf'] + 3:.0f} 年を超えたら、財務が配当を支えられない")

existing = watch["invalidation"].iloc[0] if not watch.empty else None
text = st.text_area(
    "棄却条件（監視タブが自動で見張る）",
    value=existing or "\n".join(f"・{a}" for a in auto),
    height=150,
)
thesis = st.text_area("投資仮説（市場が見落としている理由）", height=100,
                      placeholder="例：カバーするアナリストがおらず、PBR1倍割れのまま増益が続いている")

if st.button("ウォッチリストに保存", type="primary"):
    from modules.store import connect
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist "
            "(ticker, name, target_yield, bottom_yield, source, thesis, invalidation, added_at, note) "
            "VALUES (?, ?, ?, COALESCE((SELECT bottom_yield FROM watchlist WHERE ticker=?), NULL), "
            "'scout', ?, ?, COALESCE((SELECT added_at FROM watchlist WHERE ticker=?), date('now')), "
            "(SELECT note FROM watchlist WHERE ticker=?))",
            (ticker, row["name"], target, ticker, thesis, text, ticker, ticker),
        )
    st.success(f"{row['name']} をウォッチリストに保存しました")
    st.cache_data.clear()
