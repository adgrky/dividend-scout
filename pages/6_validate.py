"""検証 — スコアが本当に効くかを確かめる。

ここを通さずに重みを動かさない。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.store import read_df
from modules.validation import (Cohort, OUTCOMES, TESTABLE_FACTORS, benchmark,
                                composite_test, factor_power, quintile_table, run_cohort)

st.title("🧪 検証")
st.caption("スコアの予測力を過去データで確かめる。効かない層の重みは上げない。")

st.info("""**なぜ必ずこれを通すか**

別プロジェクト（stock-recommender）で13個の銘柄選別指標を測ったところ、相関がすべて |r| < 0.1 で
「銘柄選別に上乗せできる情報は無い」という結果が出ている。配当成長は時間軸が違うので同じ結論とは
限らないが、確かめずに信じたら同じ失敗になる。

**検証できるのは、株価と配当履歴から過去時点を再現できる層だけ**（B 増配意思 / E 割安 /
D 見過ごされ度の一部）。A 増配余力と C 原資成長は yfinance の財務が4〜5期分しか取れないため
過去時点を再現できず、**検証不能**。EDINET を入れるまで等ウェイトのまま据え置く。""")

c1, c2, c3 = st.columns(3)
with c1:
    years = st.multiselect("起点にする年（12月末）", [2012, 2014, 2016, 2018, 2020],
                           default=[2014, 2016, 2018, 2020])
with c2:
    horizon = st.number_input("先の年数", 3, 8, 5)
with c3:
    outcome_label = st.selectbox("見る実績", list(OUTCOMES.keys()), index=1)
outcome_col = OUTCOMES[outcome_label]

if not years:
    st.stop()

run = st.button("検証を実行", type="primary")
if not run and "validation_frames" not in st.session_state:
    st.caption("実行には数分かかります。コマンドラインでも同じ検証ができます："
               "`uv run python scripts/validate_score.py`")
    st.stop()

if run:
    prices = read_df("SELECT ticker, date, close, volume FROM prices ORDER BY ticker, date")
    div = read_df("SELECT ticker, date, amount FROM dividends")
    if prices.empty:
        st.warning("株価データがありません。先に `uv run python scripts/weekly_scan.py` を実行してください。")
        st.stop()

    frames = {}
    bar = st.progress(0.0, "コホートを構築中...")
    for i, y in enumerate(sorted(years)):
        bar.progress(i / len(years), f"{y}年末コホートを構築中...")
        df = run_cohort(prices, div, Cohort(pd.Timestamp(f"{y}-12-31"), int(horizon)))
        if not df.empty:
            frames[y] = df
    bar.progress(1.0, "完了")
    st.session_state["validation_frames"] = frames

frames = st.session_state.get("validation_frames", {})
if not frames:
    st.warning("有効なコホートを作れませんでした")
    st.stop()

allc = pd.concat(frames.values(), ignore_index=True)
st.success(f"{len(frames)} コホート / 延べ {len(allc):,} 銘柄で検証しました")

tab1, tab2, tab3, tab4 = st.tabs(["指標の説明力", "分位別の実績", "ベンチマーク比較", "前半→後半の答え合わせ"])

with tab1:
    power = factor_power(allc)
    pivot = power.pivot(index="指標", columns="実績", values="相関")
    st.dataframe(pivot.round(3), width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.3f") for c in pivot.columns})
    st.caption("スピアマンの順位相関。|相関| < 0.05 はノイズ。0.10 を超えたら意味がある可能性がある。")
    if power["相関"].abs().max() < 0.10:
        st.error("0.10 を超えた指標が1つもありません。"
                 "**スコアで銘柄を選べるという前提そのものを疑う必要があります。**")

    st.subheader("コホート別（時期をまたいで安定しているか）")
    rows = []
    for y, df in frames.items():
        p = factor_power(df)
        p["コホート"] = y
        rows.append(p)
    by_cohort = pd.concat(rows)
    sub = by_cohort[by_cohort["実績"] == outcome_label]
    if not sub.empty:
        st.dataframe(sub.pivot(index="指標", columns="コホート", values="相関").round(3),
                     width="stretch")
        st.caption("符号がコホートごとに入れ替わる指標は、効いていないと考えるべき。")

with tab2:
    factor_label = st.selectbox("指標", list(TESTABLE_FACTORS.keys()))
    t = quintile_table(allc, TESTABLE_FACTORS[factor_label], outcome_col)
    if t.empty:
        st.caption("データが足りません")
    else:
        fig = go.Figure(go.Bar(x=t["分位"].astype(str), y=t["平均"] * 100,
                               marker_color="#4C8BF5"))
        fig.update_layout(height=320, yaxis_title=f"{outcome_label}（%）",
                          margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
        st.dataframe(t, hide_index=True, width="stretch")
        st.caption("Q5 が最上位。Q1→Q5 で単調に上がっていれば本物の可能性がある。"
                   "途中で山や谷があるなら、たまたま。")

with tab3:
    bench = benchmark(allc)
    st.dataframe(bench, hide_index=True, width="stretch",
                 column_config={
                     "トータルリターン": st.column_config.NumberColumn(format="%.1f%%"),
                     "DPS成長": st.column_config.NumberColumn(format="%.1f%%"),
                     "減配発生率": st.column_config.NumberColumn(format="%.1f%%"),
                 })
    st.caption("スコアがこれらに勝てないなら、複雑なスコアを使う理由がない。")

with tab4:
    ys = sorted(frames)
    if len(ys) < 2:
        st.caption("コホートが1つしかないので分割できません")
    else:
        half = len(ys) // 2 or 1
        train = pd.concat([frames[y] for y in ys[:half]], ignore_index=True)
        test = pd.concat([frames[y] for y in ys[half:]], ignore_index=True)
        st.caption(f"学習: {ys[:half]} → 検証: {ys[half:]}　"
                   "全期間の最良値でチューニングすると、後から何の意味もない数字が出てくる。")
        res = composite_test(train, test, outcome=outcome_col)
        st.dataframe(res, hide_index=True, width="stretch")
