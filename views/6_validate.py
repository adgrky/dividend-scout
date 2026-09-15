"""検証 — スコアが本当に効くかを確かめる。

ここを通さずに重みを動かさない。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.format import to_pct
from modules.store import read_df
from modules.validation import (Cohort, OUTCOMES, TESTABLE_FACTORS, benchmark,
                                composite_test, factor_power, quintile_table, run_cohort)

st.title("🧪 検証")
st.caption("スコアの予測力を過去データで確かめる。効かない層の重みは上げない。")

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

_CACHE = Path(__file__).resolve().parent.parent / "data" / "validation.csv"


def _load_cached() -> dict:
    """前回の検証結果を読み直す。

    コホートの構築には数分かかる。アプリを開くたびに計算し直していたら
    誰も検証タブを見なくなるので、結果はファイルに残して次回は読むだけにする。
    """
    if not _CACHE.exists():
        return {}
    try:
        df = pd.read_csv(_CACHE)
        return {int(str(a)[:4]): g for a, g in df.groupby("asof")}
    except Exception:
        return {}


if "validation_frames" not in st.session_state:
    cached = _load_cached()
    if cached:
        st.session_state["validation_frames"] = cached
        st.info(f"前回の検証結果を読み込みました（{len(cached)} コホート・"
                f"{_CACHE.stat().st_mtime and datetime.fromtimestamp(_CACHE.stat().st_mtime):%Y-%m-%d %H:%M} 時点）。"
                "条件を変えたいときは下の「検証を実行」を押してください。")

run = st.button("検証を実行（数分かかります）", type="primary")
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
    if frames:
        # 次に開いたときに計算し直さなくて済むよう保存する
        pd.concat(frames.values(), ignore_index=True).to_csv(
            _CACHE, index=False, encoding="utf-8-sig")

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
    st.markdown("**表の数字 ＝ 順位相関**。"
                "「その指標が高かった銘柄は、実際にその後どうだったか」を −1〜+1 で表したもの。")
    st.markdown("""
| 数字 | 読み方 |
|---|---|
| **+0.10 以上** | その指標が高いほど結果も良かった。**使える可能性がある** |
| −0.05 〜 +0.05 | ほぼ無関係。**ノイズ**。スコアに入れる理由がない |
| **−0.10 以下** | その指標が高いほど結果は悪かった。**逆に使うべき** |

+1 は完全に一致、0 は無関係、−1 は完全に逆。株の世界では 0.10 でも十分大きい数字です。
""")
    st.dataframe(pivot.round(3), width="stretch", column_config={
        c: st.column_config.NumberColumn(
            format="%.3f",
            help="+0.10以上なら使える可能性／±0.05以内はノイズ／−0.10以下は逆に効いている")
        for c in pivot.columns})
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
        st.caption("**縦が指標、横が起点にした年**。同じ指標が年によってプラスになったり"
                   "マイナスになったりするなら、たまたまであって効いていません。"
                   "どの年も同じ符号で 0.10 を超えている指標だけが本物です。")

with tab2:
    factor_label = st.selectbox("指標", list(TESTABLE_FACTORS.keys()))
    t = quintile_table(allc, TESTABLE_FACTORS[factor_label], outcome_col)
    if t.empty:
        st.caption("データが足りません")
    else:
        fig = go.Figure(go.Bar(x=t["分位"].astype(str), y=to_pct(t["中央値"]),
                               marker_color="#4C8BF5"))
        fig.update_layout(height=320, yaxis_title=f"{outcome_label}（%）",
                          margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
        tv = t.copy()
        for col in ("平均", "中央値"):
            tv[col] = to_pct(tv[col])
        st.dataframe(tv, hide_index=True, width="stretch",
                     column_config={"平均": st.column_config.NumberColumn(format="%.1f%%"),
                                    "中央値": st.column_config.NumberColumn(format="%.1f%%")})
        st.caption("**銘柄をその指標の順に5組へ分けて、その後の実績を組ごとに集計したもの。** "
                   "Q1 が最下位の2割、Q5 が最上位の2割。「平均」は裾の極端な銘柄に引っ張られるので、"
                   "**中央値を見てください**（真ん中の銘柄がどうだったか）。\n\n"
                   "Q1→Q5 でなだらかに上がっていれば本物の可能性があります。"
                   "途中で山や谷があるなら、たまたまです。")

with tab3:
    bench = benchmark(allc).copy()
    pct_cols = ["リターン中央値", "リターン平均（裾を刈る）", "DPS成長 中央値", "減配発生率"]
    for col in pct_cols:
        bench[col] = to_pct(bench[col])
    st.dataframe(bench, hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.1f%%")
                                for c in pct_cols})
    st.markdown("""
**列の意味**

| 列 | 何の数字か |
|---|---|
| リターン中央値 | 買って持っていた期間の値上がり＋配当。**真ん中の銘柄**がどうだったか |
| リターン平均（裾を刈る） | 上下5%の極端な銘柄を除いた平均 |
| DPS成長 中央値 | 1株あたり配当が何%増えたか |
| 減配発生率 | その組のうち、期間中に減配した銘柄の割合。**低いほど良い** |

いちばん上の行が「何も考えず全銘柄を等分で持った場合」です。
**スコアで選んだ組がこれに勝てないなら、複雑なスコアを使う理由がありません。**
""")

with tab4:
    ys = sorted(frames)
    if len(ys) < 2:
        st.caption("コホートが1つしかないので分割できません")
    else:
        half = len(ys) // 2 or 1
        train = pd.concat([frames[y] for y in ys[:half]], ignore_index=True)
        test = pd.concat([frames[y] for y in ys[half:]], ignore_index=True)
        st.markdown(f"""
**前半 {ys[:half]} 年のデータだけで重みを決めて、後半 {ys[half:]} 年で答え合わせをします。**

全期間をまとめて最良の重みを探すと、過去にだけよく当てはまる数字が出てきて、
これから先にはまったく効きません。だから必ず分けます。

下の表は「前半で決めた重みを後半に当てたら、実際どうだったか」。
**後半でも上位の組が上位のままなら本物**、順位が入れ替わるなら偶然です。
""")
        res = composite_test(train, test, outcome=outcome_col).copy()
        for col in res.columns:
            if res[col].dtype.kind == "f":
                res[col] = to_pct(res[col])
        st.dataframe(res, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.1f%%")
                                    for c in res.columns if res[c].dtype.kind == "f"})
