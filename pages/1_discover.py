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
from modules.store import read_df
from modules.ui import (flash, show_flash, LAYER_LABELS, get_config, get_holdings, get_next_ex_dates,
                        get_scores,
                        get_watchlist, no_data_guard)

st.title("🔭 発掘")
show_flash()
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
# 配当がある月は配当履歴から出す（直近3年）。yfinance の exDividendDate は
# 直近の1回しか返さず、3月決算の会社は9月しか見えないため。
view["配当月"] = view["payout_months"].fillna("") if "payout_months" in view.columns else ""

# ── フィルタ ──
c1, c2, c3 = st.columns([1.1, 1.5, 1.6])
with c1:
    scope = st.radio("表示", ["新規のみ", "買い増しどき", "すべて", "保有のみ"],
                     help="「新規のみ」＝まだ持っていない・監視もしていない銘柄（発掘の主目的）。\n"
                          "「買い増しどき」＝すでに持っていて、いま自己利回り順位が高い銘柄。"
                          "配当が育っている銘柄を安く買い足せる機会です")
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
    month = st.selectbox("配当がある月", _MONTHS, index=0,
                         help="配当の受け取りが特定の月に偏っているとき、"
                              "空いている月に配当がある銘柄を探すのに使います（直近3年の実績）")
    sort_by = st.selectbox("並び順", ["買い付け優先度", "発掘スコア", "配当継続スコア",
                                   "配当利回り", "自己利回り順位", "連続増配年数", "ヘム指数"],
                           help="**買い付け優先度**＝いま買う順番（√(割安度×配当継続)）。\n"
                                "**発掘スコア**＝見つける順番（市場に気づかれていないかを含む）")

show_layers = st.checkbox("採点の内訳（5層）も表示", value=False)

f = view.copy()
if scope == "新規のみ":
    f = f[f["新規"]]
elif scope == "買い増しどき":
    # すでに持っていて、その銘柄自身の過去と比べていま安い銘柄。
    # 配当が育っている銘柄を安く買い足すのは、新規を探すのと同じくらい価値がある。
    f = f[f["保有"] & (f["yield_percentile"].fillna(0) >= 0.7)]
elif scope == "保有のみ":
    f = f[f["保有"]]
if scope in ("買い増しどき", "保有のみ"):
    st.caption(f"この画面は**採用基準を通った銘柄だけ**を並べています。"
               f"保有 {len(held)} 銘柄のうち基準を通っているのは {int(view['保有'].sum())} 銘柄です。"
               "基準を外れている保有は 🧹 資金投入 →「整理する」で見てください。")
f = f[f["dividend_yield"].fillna(0) >= min_yield]
f = f[f["streak"].fillna(0) >= min_streak]
if pick:
    f = f[f["sector33"].isin(pick)]
if hem_only:
    f = f[f["hem_ratio"].fillna(0) >= config["scoring"]["hem_ratio_threshold"]]
if month != "—":
    # 「1月」を含むかで判定すると **「11月」も「1月」を含む**ため混ざる。
    # 実測: 2月を選ぶと120件出たが、本当に2月配当なのは26件で、残り94件は
    # 12月銘柄だった。配当月の平準化という目的そのものを壊していた。
    # 「・」で区切って完全一致で見る。
    f = f[f["配当月"].fillna("").map(
        lambda s: month in [x.strip() for x in str(s).split("・") if x.strip()])]

# 資金投入と同じ式で買い付け優先度を出す。画面ごとに違う指標を見せない。
from modules.allocator import buy_priority, month_gaps          # noqa: E402
from modules.portfolio import dividend_calendar                 # noqa: E402
from modules.ui import flash, show_flash, get_positions                            # noqa: E402

_pos = get_positions(config)
# 目標利回り（保有・ウォッチに設定したもの）を貼っておく
_tg = read_df("SELECT ticker, target_yield FROM watchlist "
              "UNION SELECT ticker, target_yield FROM holdings").groupby("ticker").first()
f = f.join(_tg, on="ticker")
f = buy_priority(f, _pos, config, month_gaps(_pos, dividend_calendar(_pos)))

_SORT = {"買い付け優先度": "買い付け優先度", "発掘スコア": "total", "配当継続スコア": "health",
         "配当利回り": "dividend_yield", "自己利回り順位": "yield_percentile",
         "連続増配年数": "streak", "ヘム指数": "hem_ratio"}
f = f.nlargest(int(top_n), _SORT[sort_by])

st.markdown(f"**ゲート通過 {len(passed):,} 銘柄** ／ 条件該当 **{len(f):,} 件** "
            f"（うち未保有 {int(f['新規'].sum())} 件）")

default_target = 0.047
limit_price = (f["dps_latest"] / default_target).where(f["dps_latest"] > 0)

# 次の権利落ち日。yfinance の権利確定日は1,272社中5社しか入っていないので、
# 配当履歴から予測する（会社の発表ではない）。
_ex = get_next_ex_dates(tuple(sorted(set(f["ticker"]))))
if _ex.empty:
    _ex_label = pd.Series(dtype=object)
    _ex_days = pd.Series(dtype=float)
else:
    _ex_label = _ex.set_index("ticker")["次の権利落ち日"].astype(str)
    _ex_days = _ex.set_index("ticker")["あと何日"]

table = pd.DataFrame({
    "コード": f["code"].values,
    "銘柄名": f["name"].values,
    "業種": f["sector33"].values,
    "買い付け": f["買い付け優先度"].round(0).values,
    "発掘": f["total"].round(1).values,
    "継続": f["health"].round(0).values,
    "株価": f["last_close"].round(0).values,
    "利回り": to_pct(f["dividend_yield"]).values,
    "配当性向": to_pct(f["payout_ratio"]).values,
    "ヘム指数": f["hem_ratio"].round(2).values,
    "自己利回り順位": to_pct(f["yield_percentile"]).values,
    "連続増配": f["streak"].values,
    "DPS5年成長": to_pct(f["cagr_5y"]).values,
    "配当月": f["配当月"].values,
    "次の権利落ち": _ex_label.reindex(f["ticker"]).values,
    "あと何日": _ex_days.reindex(f["ticker"]).values,
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
        "買い付け": st.column_config.ProgressColumn(
            format="%.0f", min_value=0, max_value=100,
            help="いま買う順番。√(割安度 × 配当継続) × 補完度係数 − トラップ減点"),
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
        "配当月": st.column_config.TextColumn(help="配当の権利が確定する月（直近3年の実績）"),
        "次の権利落ち": st.column_config.TextColumn(
            help="この日までに買って持っていれば、その回の配当を受け取れます。"
                 "配当履歴からの予測で、会社の発表ではありません"),
        "あと何日": st.column_config.NumberColumn(
            format="%d 日", help="小さいほど権利落ちが近い。買うなら急ぐ"),
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
    b1, b2 = c2.columns(2)
    if b1.button("📄 カルテ", type="primary", width="stretch"):
        st.session_state["profile_ticker"] = picked["ticker"]
        st.switch_page("pages/2_profile.py")
    if b2.button("⭐ 監視に追加", width="stretch",
                 help="ウォッチリストに入れると、監視タブが減配や指値到達を見張ります"):
        from modules.store import connect
        with connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (ticker, name, target_yield, source, added_at) "
                "VALUES (?, ?, 0.047, 'scout', date('now'))",
                (picked["ticker"], picked["name"]))
        st.success(f"{picked['name']} を監視に追加しました。"
                   "カルテで棄却条件を書いておくと、そこも見張ります。")
        st.cache_data.clear()
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
