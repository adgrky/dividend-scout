"""監視 — 買ったあとに仮説が壊れていないかを見張る。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

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
else:
    n_high = int((alerts["severity"] == "high").sum())
    n_med = int((alerts["severity"] == "medium").sum())
    n_low = int((alerts["severity"] == "low").sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("🔴 重大", n_high)
    c2.metric("🟡 注意", n_med)
    c3.metric("🔵 買い場・参考", n_low)

    tabs = st.tabs(["🔴 重大", "🟡 注意", "🔵 買い場・参考", "すべて"])
    for tab, sev in zip(tabs, ["high", "medium", "low", None]):
        with tab:
            sub = alerts if sev is None else alerts[alerts["severity"] == sev]
            if sub.empty:
                st.caption("該当なし")
                continue
            for _, r in sub.iterrows():
                fn = {"high": st.error, "medium": st.warning}.get(r["severity"], st.info)
                fn(r["message"])

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
    st.dataframe(watch.rename(columns={
        "ticker": "銘柄", "name": "銘柄名", "target_yield": "目標利回り",
        "thesis": "投資仮説", "invalidation": "棄却条件"}),
        hide_index=True, width="stretch",
        column_config={"目標利回り": st.column_config.NumberColumn(format="%.2f%%")})

st.divider()
st.subheader("過去に記録したアラート")
hist = read_df("SELECT detected_at, ticker, severity, kind, message FROM alerts "
               "ORDER BY detected_at DESC LIMIT 200")
if hist.empty:
    st.caption("まだ記録がありません")
else:
    st.dataframe(hist.rename(columns={
        "detected_at": "検出日時", "ticker": "銘柄", "severity": "重要度",
        "kind": "種別", "message": "内容"}), hide_index=True, width="stretch", height=400)
