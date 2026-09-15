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
from modules.ui import flash, show_flash, get_config, get_scores, no_data_guard

st.title("🚨 監視")
show_flash()
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

KINDS = {
    "dividend_cut":     ("🔴", "減配した", "配当株にとって最も重い異変。増配を前提に買ったなら前提が消えた"),
    "dividend_broken":  ("🔴", "配当が壊れた", "利益を超えて配当している／減配を繰り返している"),
    "yield_trap":       ("🟠", "安いが買えない", "利回りは目標に届いたが、配当が危ない銘柄"),
    "dividend_weak":    ("🟡", "原資が傷んでいる", "まだ配当は出ているが、次の決算で確かめるもの"),
    "trap":             ("🟡", "高配当トラップ", "利回りの高さが減配の織り込みかもしれない"),
    "target_reached":   ("🔵", "買い場", "目標利回りに届き、かつ買う基準も通っている"),
    "take_profit":      ("🟢", "利確の検討", "壊れたのではなく育ちきった。ヘムの「上がりすぎたら売る」"),
    "dividend_flat":    ("⚪️", "増配が止まった", "据え置きが続いている"),
}

cols = st.columns(4)
for i, (kind, (icon, label, help_)) in enumerate(KINDS.items()):
    cols[i % 4].metric(f"{icon} {label}", n.get(kind, 0), help=help_)

st.info("""**判定の出どころはひとつです。**

以前このページは売る判定を自前で持っていて、同じ銘柄に正反対の指示が出ていました。

    日本製鉄　　　監視「目標利回りに到達＝買い場。良い知らせ」
    　　　　　　　整理「🔴 利益を超えて配当を出している（配当性向 2193%）」

いまは 🧹 整理する と同じ `sell_rules`、🛒 買う と同じ足切りを使っています。
**「買い場」と出るのは、目標利回りに届き、かつ買う基準も通ったものだけ**です。""")

st.divider()

# ── いま判断が要るもの ──
st.subheader("いま判断が要るもの")

for kind in ("dividend_cut", "dividend_broken"):
    g = by_kind.get(kind)
    if g is None or g.empty:
        continue
    icon, label, help_ = KINDS[kind]
    st.markdown(f"**{icon} {label}** — {help_}")
    for _, r in g.iterrows():
        st.error(r["message"])

trap_like = pd.concat([by_kind[k] for k in ("yield_trap", "trap", "dividend_weak")
                       if k in by_kind], ignore_index=True) \
    if any(k in by_kind for k in ("yield_trap", "trap", "dividend_weak")) else pd.DataFrame()
if not trap_like.empty:
    st.markdown("**🟠 安いが買えない／原資が傷んでいる** — "
                "利回りだけを見て買い増すと損をするもの")
    for _, r in trap_like.iterrows():
        st.warning(r["message"])

reached = by_kind.get("target_reached")
if reached is not None and not reached.empty:
    st.markdown("**🔵 買い場** — 目標利回りに届き、かつ買う基準も通っている")
    for _, r in reached.iterrows():
        st.info(r["message"])
else:
    st.caption("いま「買い場」と呼べる銘柄はありません。")

tp = by_kind.get("take_profit")
if tp is not None and not tp.empty:
    with st.expander(f"🟢 利確の検討（{len(tp)} 件）— 壊れたのではなく育ちきった銘柄"):
        for _, r in tp.iterrows():
            st.success(r["message"])

st.divider()

# ── 状態として成立しているだけのもの（表にたたむ）──
st.subheader("状態として続いているもの")
st.caption("毎日変わるものではないので一覧にまとめている。整理の判断材料として見る。")

standing = by_kind.get("dividend_flat", pd.DataFrame())
if standing.empty:
    st.caption("該当なし")
else:
    tbl = pd.DataFrame({
        "コード": standing["code"],
        "銘柄名": standing["name"],
        "内容": standing["message"].str.split(": ").str[-1],
    })
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
