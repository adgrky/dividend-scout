"""発掘 — このアプリの本丸。

全上場からゲートを通った銘柄をスコア順に並べ、ケンがまだ持っていない・
見ていない銘柄を最優先で浮かび上がらせる。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.format import csv_bytes, yen
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

passed = scores[scores["gate_passed"] == 1].copy()
passed["保有"] = passed["ticker"].isin(held)
passed["監視中"] = passed["ticker"].isin(watched)
passed["新規"] = ~(passed["保有"] | passed["監視中"])

detail = passed["detail_json"].map(lambda s: pd.json_normalize(pd.read_json(s, typ="series")["raw"]).iloc[0]
                                   if isinstance(s, str) else pd.Series(dtype=float))
raw = pd.DataFrame(list(detail)).set_index(passed.index)
view = pd.concat([passed, raw], axis=1)

# ── フィルタ ──
c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.6, 1])
with c1:
    scope = st.radio("表示", ["新規のみ", "すべて", "保有のみ"], horizontal=False,
                     help="「新規のみ」＝まだ持っていない・監視もしていない銘柄。発掘の主目的はここ。")
with c2:
    min_yield = st.slider("最低利回り", 0.0, 8.0, 3.0, 0.25, format="%.2f%%") / 100
    min_streak = st.number_input("連続増配 最低年数", 0, 30, 0)
with c3:
    sectors = sorted(view["sector33"].dropna().unique())
    pick = st.multiselect("業種（33業種）", sectors, default=[])
with c4:
    top_n = st.number_input("表示件数", 10, 300, 50, step=10)

f = view.copy()
if scope == "新規のみ":
    f = f[f["新規"]]
elif scope == "保有のみ":
    f = f[f["保有"]]
f = f[f["dividend_yield"].fillna(0) >= min_yield]
f = f[f["streak"].fillna(0) >= min_streak]
if pick:
    f = f[f["sector33"].isin(pick)]
f = f.nlargest(int(top_n), "total")

st.markdown(
    f"**ゲート通過 {len(passed):,} 銘柄** ／ 条件該当 {len(f):,} 件 "
    f"（うち未保有 {int(f['新規'].sum())} 件）"
)

table = pd.DataFrame({
    "コード": f["code"],
    "銘柄名": f["name"],
    "業種": f["sector33"],
    "総合": f["total"].round(1),
    "増配余力": f["capacity"].round(0),
    "増配意思": f["willingness"].round(0),
    "原資成長": f["growth"].round(0),
    "見過ごされ度": f["neglect"].round(0),
    "割安": f["valuation"].round(0),
    "減点": (-f["trap_penalty"]).round(0),
    "株価": f["last_close"].round(0),
    "利回り": f["dividend_yield"],
    "自己利回り順位": f["yield_percentile"],
    "連続増配": f["streak"],
    "DPS5年": f["cagr_5y"],
    "配当性向": f["payout_ratio"],
    "保有": f["保有"],
})

st.dataframe(
    table, width="stretch", hide_index=True, height=620,
    column_config={
        "利回り": st.column_config.NumberColumn(format="%.2f%%",
                                             help="直近12ヶ月の実績配当 ÷ 株価"),
        "自己利回り順位": st.column_config.ProgressColumn(
            format="%.0f%%", min_value=0, max_value=1,
            help="その銘柄自身の過去7年の利回り分布の中での位置。100%に近いほど自分史上まれに見る高利回り＝割安"),
        "DPS5年": st.column_config.NumberColumn(format="%.1f%%", help="配当の5年年率成長率"),
        "配当性向": st.column_config.NumberColumn(format="%.0f%%"),
        "総合": st.column_config.ProgressColumn(format="%.1f", min_value=0, max_value=100),
        "株価": st.column_config.NumberColumn(format="¥%d"),
    },
)

st.download_button("CSV をダウンロード", csv_bytes(table),
                   file_name="discover.csv", mime="text/csv")

with st.expander("スコアの読み方"):
    st.markdown("""
| 層 | 何を見ているか |
|---|---|
| **増配余力** | 増配を「出せる」か。配当性向・FCFカバー率・ネットキャッシュ・有利子負債の軽さ。配当性向は低いほど良いのではなく 30〜50% が満点で、15% 未満は還元意思なしとして減点する |
| **増配意思** | 増配を「する気がある」か。連続増配年数・減配なし継続年数・DPS の5年10年成長。日本株では累進配当や DOE を掲げる企業を拾うためにここが効く |
| **原資成長** | 配当の元手が伸びているか。純利益・営業CFの伸び、ROE、営業利益率 |
| **見過ごされ度** | 市場が見ていないか。規模の小ささ・売買代金の薄さ・機関投資家保有の低さはアナリストのカバー不足を表す。PBR1倍割れ×増益は東証の資本効率改善要請が最も効く位置 |
| **割安** | その銘柄**自身の過去**と比べて割安か。絶対利回りは業種特性に引きずられるので主役にしない |
| **減点** | 高配当トラップ。異常高利回り×52週安値圏、利益と営業CFの乖離、配当性向の急上昇、希薄化、利益の連続減少 |

各層は0〜100。ゲートを通った銘柄の中でのパーセンタイル順位で正規化している。

重みは現在すべて等ウェイト。**どの層が本当に効くかは「検証」タブで確かめてから重みを変える。**
""")
