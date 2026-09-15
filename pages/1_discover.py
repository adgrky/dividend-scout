"""発掘 — このアプリの本丸。

全上場からゲートを通った銘柄をスコア順に並べ、まだ持っていない・見ていない銘柄を
最優先で浮かび上がらせる。行を選ぶとそのままカルテへ飛べる。

【表に渡す数値の約束】
0〜1 の比率は必ず modules.format.to_pct を通す。Streamlit の format="%.2f%%" は
値を100倍してくれないので、直さないと 5.95% が "0.06%" と表示される。
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from modules.format import csv_bytes, to_pct
from modules.ui import (LAYER_LABELS, get_config, get_holdings, get_scores,
                        get_watchlist, no_data_guard)

st.title("🔭 発掘")
st.caption("市場に正しく評価されていない増配期待企業を全上場から探す")

config = get_config()
scores = get_scores()
if no_data_guard(scores):
    st.stop()

held = set(get_holdings()["ticker"])
watched = set(get_watchlist()["ticker"])

passed = scores[scores["gate_passed"] == 1].copy().reset_index(drop=True)
passed["保有"] = passed["ticker"].isin(held)
passed["新規"] = ~(passed["ticker"].isin(held) | passed["ticker"].isin(watched))

raw = pd.DataFrame([json.loads(x)["raw"] for x in passed["detail_json"]])
view = pd.concat([passed, raw], axis=1)

_MONTHS = ["—"] + [f"{m}月" for m in range(1, 13)]
# 権利確定日はまだ取得途中のことがある。列が無い／全て欠損でも落ちないようにする。
if "ex_dividend_date" in view.columns:
    view["権利確定月"] = pd.to_datetime(view["ex_dividend_date"], errors="coerce").dt.month
else:
    view["権利確定月"] = pd.Series([pd.NA] * len(view), index=view.index)
view["権利確定月ラベル"] = view["権利確定月"].map(
    lambda m: f"{int(m)}月" if pd.notna(m) else "—")

# ── フィルタ ──
c1, c2, c3 = st.columns([1.1, 1.5, 1.6])
with c1:
    scope = st.radio("表示", ["新規のみ", "すべて", "保有のみ"],
                     help="「新規のみ」＝まだ持っていない・監視もしていない銘柄。発掘の主目的はここ")
    top_n = st.number_input("表示件数", 10, 400, 50, step=10)
with c2:
    min_yield = st.slider("最低利回り（%）", 0.0, 8.0, 3.0, 0.25) / 100
    min_streak = st.number_input("連続増配 最低年数", 0, 30, 0)
    hem_only = st.checkbox(
        "ヘム基準を満たすものだけ", value=False,
        help="配当性向 < 配当利回り×10（＝PER10倍以下と同じ意味）。"
             "高利回りと低配当性向を同時に要求するので、"
             "「利回りは高いが配当が育たない」銘柄を落とせます")
with c3:
    sectors = sorted(view["sector33"].dropna().unique())
    pick = st.multiselect("業種（33業種）", sectors, default=[])
    month = st.selectbox("権利確定月", _MONTHS, index=0,
                         help="配当の受け取りが特定の月に偏っているとき、"
                              "空いている月に権利確定する銘柄を探すのに使います")
    sort_by = st.selectbox("並び順", ["総合スコア", "配当継続スコア", "配当利回り",
                                   "自己利回り順位", "連続増配年数", "ヘム指数"])

show_layers = st.checkbox("採点の内訳（5層）も表示", value=False)

f = view.copy()
if scope == "新規のみ":
    f = f[f["新規"]]
elif scope == "保有のみ":
    f = f[f["保有"]]
f = f[f["dividend_yield"].fillna(0) >= min_yield]
f = f[f["streak"].fillna(0) >= min_streak]
if pick:
    f = f[f["sector33"].isin(pick)]
if hem_only:
    f = f[f["hem_ratio"].fillna(0) >= config["scoring"]["hem_ratio_threshold"]]
if month != "—":
    f = f[f["権利確定月"] == int(month.replace("月", ""))]

_SORT = {"総合スコア": "total", "配当継続スコア": "health", "配当利回り": "dividend_yield",
         "自己利回り順位": "yield_percentile", "連続増配年数": "streak", "ヘム指数": "hem_ratio"}
f = f.nlargest(int(top_n), _SORT[sort_by])

st.markdown(f"**ゲート通過 {len(passed):,} 銘柄** ／ 条件該当 **{len(f):,} 件** "
            f"（うち未保有 {int(f['新規'].sum())} 件）")

default_target = 0.047
limit_price = (f["dps_latest"] / default_target).where(f["dps_latest"] > 0)

table = pd.DataFrame({
    "コード": f["code"].values,
    "銘柄名": f["name"].values,
    "業種": f["sector33"].values,
    "発掘": f["total"].round(1).values,
    "継続": f["health"].round(0).values,
    "株価": f["last_close"].round(0).values,
    "利回り": to_pct(f["dividend_yield"]).values,
    "配当性向": to_pct(f["payout_ratio"]).values,
    "ヘム指数": f["hem_ratio"].round(2).values,
    "自己利回り順位": to_pct(f["yield_percentile"]).values,
    "連続増配": f["streak"].values,
    "DPS5年成長": to_pct(f["cagr_5y"]).values,
    "権利確定": f["権利確定月ラベル"].values,
    f"指値({default_target:.1%})": limit_price.round(0).values,
    "保有": f["保有"].values,
})
if show_layers:
    for key, label in LAYER_LABELS.items():
        table[label] = f[key].round(0).values
    table["減点"] = (-f["trap_penalty"]).round(0).values

event = st.dataframe(
    table, width="stretch", hide_index=True, height=560,
    on_select="rerun", selection_mode="single-row",
    column_config={
        "発掘": st.column_config.ProgressColumn(
            format="%.1f", min_value=0, max_value=100,
            help="市場にまだ気づかれていない増配候補としての点数。大型株は構造的に低く出ます"),
        "継続": st.column_config.NumberColumn(
            format="%.0f", help="配当が続くか・増えるかだけを見た点数。持っている株の評価はこちら"),
        "株価": st.column_config.NumberColumn(format="¥%d"),
        "利回り": st.column_config.NumberColumn(format="%.2f%%", help="直近12ヶ月の実績配当 ÷ 株価"),
        "配当性向": st.column_config.NumberColumn(
            format="%.0f%%", help="配当総額 ÷ 純利益。30〜50%が理想。低すぎるのは還元意思が薄い"),
        "ヘム指数": st.column_config.NumberColumn(
            format="%.2f", help="配当利回り×10 ÷ 配当性向。1.00以上でヘムの基準を満たす"),
        "自己利回り順位": st.column_config.ProgressColumn(
            format="%.0f%%", min_value=0, max_value=100,
            help="その銘柄自身の過去7年の利回り分布の中での位置。"
                 "100%に近いほど自分史上まれに見る高利回り＝割安"),
        "連続増配": st.column_config.NumberColumn(format="%d 年"),
        "DPS5年成長": st.column_config.NumberColumn(format="%.1f%%", help="1株配当の5年の年率成長率"),
        "権利確定": st.column_config.TextColumn(help="直近の権利確定日の月"),
        f"指値({default_target:.1%})": st.column_config.NumberColumn(
            format="¥%d", help=f"直近の実績配当で利回り{default_target:.1%}に届く株価"),
    },
)

# ── 選んだ行からカルテへ ──
sel = event.selection.rows if event and event.selection else []
if sel:
    i = sel[0]
    picked = f.iloc[i]
    c1, c2 = st.columns([3, 1])
    c1.info(f"**{picked['code']} {picked['name']}**（{picked['sector33']}）　"
            f"利回り {picked['dividend_yield']:.2%}／自己利回り順位 {picked['yield_percentile']:.0%}"
            f"／連続増配 {int(picked['streak'] or 0)}年")
    if c2.button("📄 この銘柄のカルテを見る", type="primary", width="stretch"):
        st.session_state["profile_ticker"] = picked["ticker"]
        st.switch_page("pages/2_profile.py")
else:
    st.caption("💡 行をクリックすると、その銘柄のカルテに移動できます。")

st.download_button("CSV をダウンロード", csv_bytes(table),
                   file_name="discover.csv", mime="text/csv")

with st.expander("この表の読み方（詳しくは「使い方」タブ）"):
    st.markdown("""
見るべきは3つだけです。

| 列 | 読み方 |
|---|---|
| **自己利回り順位** | その銘柄**自身の過去7年**の中で、いまの利回りがどの位置か。90%以上なら「この会社としては、めったにない高利回り」 |
| **ヘム指数** | 1.00以上なら「高利回りなのに配当性向が低い」＝増配の余地がまだある |
| **連続増配** | 長いほど、配当を増やす姿勢が定着している |

**発掘スコアと配当継続スコアは別物です。**
発掘は「市場にまだ気づかれていないか」を含むので、大型株は構造的に低く出ます。
持っている株を評価するときは「継続」を見てください。

**総合点だけで買わないこと。** 高利回りは減配の前触れであることもあります。
必ずカルテで「利回りが上がったのは株価が下がったからか、配当が増えたからか」を確かめてください。
""")
