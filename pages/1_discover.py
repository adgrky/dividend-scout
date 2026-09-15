"""発掘 — このアプリの本丸。

全上場からゲートを通った銘柄をスコア順に並べ、ケンがまだ持っていない・
見ていない銘柄を最優先で浮かび上がらせる。

【表に渡す数値の約束】
利回りや配当性向のような 0〜1 の比率は、必ず modules.format.to_pct を通して
「パーセントの数値」に直してから渡す。Streamlit の format="%.2f%%" は値を
100倍してくれないので、直さないと 5.95% が "0.06%" と表示される。
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
passed["監視中"] = passed["ticker"].isin(watched)
passed["新規"] = ~(passed["保有"] | passed["監視中"])

raw = pd.DataFrame([json.loads(x)["raw"] for x in passed["detail_json"]])
view = pd.concat([passed, raw], axis=1)

# ── フィルタ ──
c1, c2, c3 = st.columns([1.1, 1.5, 1.6])
with c1:
    scope = st.radio("表示", ["新規のみ", "すべて", "保有のみ"],
                     help="「新規のみ」＝まだ持っていない・監視もしていない銘柄。発掘の主目的はここ。")
    top_n = st.number_input("表示件数", 10, 400, 50, step=10)
with c2:
    min_yield = st.slider("最低利回り（%）", 0.0, 8.0, 3.0, 0.25) / 100
    min_streak = st.number_input("連続増配 最低年数", 0, 30, 0)
    hem_only = st.checkbox(
        "ヘム基準を満たすものだけ", value=False,
        help="配当性向 < 配当利回り×10（＝PER<10と同値）。高利回りと低配当性向を"
             "同時に要求する条件で、「利回りは高いが配当が育たない」銘柄を落とせる。")
with c3:
    sectors = sorted(view["sector33"].dropna().unique())
    pick = st.multiselect("業種（33業種）", sectors, default=[])
    show_layers = st.checkbox("採点の内訳（5層）も表示", value=False)
    sort_by = st.selectbox("並び順", ["総合スコア", "配当利回り", "自己利回り順位",
                                   "連続増配年数", "ヘム指数"])

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

_SORT_COL = {"総合スコア": "total", "配当利回り": "dividend_yield",
             "自己利回り順位": "yield_percentile", "連続増配年数": "streak",
             "ヘム指数": "hem_ratio"}
f = f.nlargest(int(top_n), _SORT_COL[sort_by])

st.markdown(
    f"**ゲート通過 {len(passed):,} 銘柄** ／ 条件該当 {len(f):,} 件 "
    f"（うち未保有 {int(f['新規'].sum())} 件）"
)

# 目標利回りに届く株価＝指値。ここが「いくらで買えばいいか」の答えになる。
default_target = 0.047
limit_price = (f["dps_latest"] / default_target).where(f["dps_latest"] > 0)

table = pd.DataFrame({
    "コード": f["code"].values,
    "銘柄名": f["name"].values,
    "業種": f["sector33"].values,
    "総合": f["total"].round(1).values,
    "株価": f["last_close"].round(0).values,
    "利回り": to_pct(f["dividend_yield"]).values,
    "配当性向": to_pct(f["payout_ratio"]).values,
    "ヘム指数": f["hem_ratio"].round(2).values,
    "自己利回り順位": to_pct(f["yield_percentile"]).values,
    "連続増配": f["streak"].values,
    "DPS5年成長": to_pct(f["cagr_5y"]).values,
    f"指値({default_target:.1%})": limit_price.round(0).values,
    "保有": f["保有"].values,
})
if show_layers:
    for key, label in LAYER_LABELS.items():
        table[label] = f[key].round(0).values
    table["減点"] = (-f["trap_penalty"]).round(0).values

st.dataframe(
    table, width="stretch", hide_index=True, height=620,
    column_config={
        "総合": st.column_config.ProgressColumn(
            format="%.1f", min_value=0, max_value=100,
            help="6層の加重合成。重みは config.yaml、根拠は検証タブ"),
        "株価": st.column_config.NumberColumn(format="¥%d"),
        "利回り": st.column_config.NumberColumn(
            format="%.2f%%", help="直近12ヶ月の実績配当 ÷ 株価"),
        "配当性向": st.column_config.NumberColumn(
            format="%.0f%%", help="配当総額 ÷ 純利益。低いほど増配の余力がある"),
        "ヘム指数": st.column_config.NumberColumn(
            format="%.2f",
            help="配当利回り×10 ÷ 配当性向。1.00 以上でヘムの「配当性向 < 利回り×10」を満たす"),
        "自己利回り順位": st.column_config.ProgressColumn(
            format="%.0f%%", min_value=0, max_value=100,
            help="その銘柄自身の過去7年の利回り分布の中での位置。"
                 "100%に近いほど自分史上まれに見る高利回り＝割安"),
        "連続増配": st.column_config.NumberColumn(format="%d 年"),
        "DPS5年成長": st.column_config.NumberColumn(
            format="%.1f%%", help="1株配当の5年の年率成長率"),
        f"指値({default_target:.1%})": st.column_config.NumberColumn(
            format="¥%d", help=f"直近の実績配当で利回り{default_target:.1%}に届く株価"),
    },
)

st.download_button("CSV をダウンロード", csv_bytes(table),
                   file_name="discover.csv", mime="text/csv")

with st.expander("スコアの読み方"):
    st.markdown("""
| 層 | 何を見ているか |
|---|---|
| **増配余力** | 増配を「出せる」か。配当性向・**ヘム指数**・FCFカバー率・ネットキャッシュ・有利子負債の軽さ。配当性向は低いほど良いのではなく 30〜50% が満点で、15% 未満は還元意思なしとして減点する |
| **増配意思** | 増配を「する気がある」か。連続増配年数・減配なし継続年数・DPS の5年10年成長 |
| **原資成長** | 配当の元手が伸びているか。純利益・営業CFの伸び、ROE、営業利益率 |
| **見過ごされ度** | 市場が見ていないか。規模の小ささ・売買代金の薄さ・機関投資家保有の低さはアナリストのカバー不足を表す。PBR1倍割れ×増益は東証の資本効率改善要請が最も効く位置 |
| **割安** | その銘柄**自身の過去**と比べて割安か。絶対利回りは業種特性に引きずられるので主役にしない |
| **減点** | 高配当トラップ。異常高利回り×52週安値圏、利益と営業CFの乖離、配当性向の急上昇、希薄化、利益の連続減少 |

各層は0〜100で、ゲートを通った銘柄の中でのパーセンタイル順位。

**重みは 2026-09-15 の検証結果にもとづく**（4時点・延べ9,220銘柄）。

| 層 | 重み | 根拠 |
|---|---|---|
| 割安 | 1.5 | 検証で唯一きれいに単調だった（分位別リターン +15% → +65%） |
| 増配余力 | 1.5 | ヘムの選定基準の1番目。割安と組んで初めて意味を持つ |
| 見過ごされ度 | 1.2 | 弱いが単調（+32% → +46%）。機関投資家が入れない領域 |
| 原資成長 | 1.0 | 財務が4〜5期しか取れず検証不能のため据え置き |
| **増配意思** | **0.5** | **加点としては効かなかった。連続増配上位30社は全体平均に負けた。減配歴はゲート側で効かせている** |

**ヘム指数**：配当利回り×10 ÷ 配当性向。1.00 以上が「配当性向 < 配当利回り×10」を満たす。
利回り単独で買うと配当が育たない（検証: 利回り上位30社の5年DPS成長は **-3.8%**）のは、
配当性向の高い銘柄が混ざるため。高利回りと低配当性向を同時に要求することで、
「利回りは高いが増配余力が無い」銘柄を落とす。ただし**この効果は過去の配当性向データが
無いため検証できていない**（EDINET を入れるまでは理屈として採用している）。
""")
