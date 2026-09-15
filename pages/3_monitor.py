"""監視 — 買ったあとに仮説が壊れていないかを見張る。

95件の赤い箱を並べても行動できない。種別ごとにまとめ、
「いま手を動かすべきもの」を上に、「状態として成立しているだけのもの」を
表にたたんで下に置く。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.format import to_pct
from modules.monitor import build_alerts, save_alerts
from modules.store import read_df
from modules.ui import get_config, get_scores, no_data_guard

st.title("🚨 監視")
st.caption("保有とウォッチリストの異変。株価の下落そのものはアラートにしない（配当株では買い場になるため）")

config = get_config()
if no_data_guard(get_scores()):
    st.stop()

if st.button("いま検出し直す"):
    st.cache_data.clear()


@st.cache_data(ttl=300, show_spinner="異変を検出中...")
def _alerts(_config: dict) -> pd.DataFrame:
    return build_alerts(_config)


alerts = _alerts(config)
if alerts.empty:
    st.success("いまのところ異変はありません")
    st.stop()

names = read_df("SELECT ticker, code, name FROM universe").set_index("ticker")
alerts = alerts.join(names, on="ticker")

by_kind = {k: g for k, g in alerts.groupby("kind")}
n = {k: len(g) for k, g in by_kind.items()}

c1, c2, c3, c4 = st.columns(4)
c1.metric("🔴 減配した", n.get("dividend_cut", 0), help="配当株にとって最も重い異変")
c2.metric("🔵 指値に届いた", n.get("target_reached", 0), help="買い場。良い知らせ")
c3.metric("⚠️ 基準を外れた", n.get("gate_failed", 0), help="買う理由が消えた状態")
c4.metric("➖ 増配が止まった", n.get("dividend_flat", 0), help="据え置きが続いている")

st.divider()

# ── いま手を動かすべきもの ──
st.subheader("いま判断が要るもの")

cut = by_kind.get("dividend_cut")
if cut is not None and not cut.empty:
    st.markdown("**減配した銘柄** — 保有し続ける理由があるか確認する")
    for _, r in cut.iterrows():
        st.error(r["message"])
else:
    st.caption("減配した銘柄はありません")

reached = by_kind.get("target_reached")
if reached is not None and not reached.empty:
    st.markdown("**目標利回りに届いた銘柄** — 買い増しの候補")
    for _, r in reached.iterrows():
        st.info(r["message"])

trap = by_kind.get("trap")
if trap is not None and not trap.empty:
    st.markdown("**高配当トラップの兆候**")
    for _, r in trap.iterrows():
        st.warning(r["message"])

st.divider()

# ── 状態として成立しているだけのもの（表にたたむ）──
st.subheader("状態として続いているもの")
st.caption("毎日変わるものではないので一覧にまとめている。整理の判断材料として見る。")

standing = pd.concat([g for k, g in by_kind.items()
                      if k in ("gate_failed", "dividend_flat")], ignore_index=True) \
    if any(k in by_kind for k in ("gate_failed", "dividend_flat")) else pd.DataFrame()

if standing.empty:
    st.caption("該当なし")
else:
    kind_label = {"gate_failed": "基準を外れた", "dividend_flat": "増配が止まった"}
    tbl = pd.DataFrame({
        "コード": standing["code"],
        "銘柄名": standing["name"],
        "種別": standing["kind"].map(kind_label).fillna(standing["kind"]),
        "内容": standing["message"].str.split(": ").str[-1],
    })
    pick = st.multiselect("種別で絞る", sorted(tbl["種別"].unique()), default=[])
    if pick:
        tbl = tbl[tbl["種別"].isin(pick)]
    st.dataframe(tbl, hide_index=True, width="stretch", height=380,
                 column_config={"内容": st.column_config.TextColumn(width="large")})

if st.button("この検出結果を記録する"):
    new = save_alerts(alerts)
    st.success(f"{len(new)} 件を新規に記録しました"
               if len(new) else "新しいアラートはありませんでした")

st.divider()
st.subheader("棄却条件を設定している銘柄")
watch = read_df("SELECT ticker, name, target_yield, thesis, invalidation FROM watchlist "
                "WHERE invalidation IS NOT NULL AND invalidation != ''")
if watch.empty:
    st.caption("まだありません。銘柄カルテで棄却条件を保存すると、ここで一覧になります。")
else:
    w = watch.rename(columns={
        "ticker": "銘柄", "name": "銘柄名", "target_yield": "目標利回り",
        "thesis": "投資仮説", "invalidation": "棄却条件"}).copy()
    w["目標利回り"] = to_pct(w["目標利回り"])
    st.dataframe(w, hide_index=True, width="stretch",
                 column_config={"目標利回り": st.column_config.NumberColumn(format="%.2f%%")})

with st.expander("過去に記録したアラート"):
    hist = read_df("SELECT detected_at, ticker, severity, kind, message FROM alerts "
                   "ORDER BY detected_at DESC LIMIT 200")
    if hist.empty:
        st.caption("まだ記録がありません")
    else:
        st.dataframe(hist.rename(columns={
            "detected_at": "検出日時", "ticker": "銘柄", "severity": "重要度",
            "kind": "種別", "message": "内容"}), hide_index=True, width="stretch", height=400)
