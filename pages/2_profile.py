"""銘柄カルテ — 1銘柄を1枚で判断できるようにする。

出すのは5つ。
    0. 何をやっている会社か（有価証券報告書の「事業の内容」）
    1. なぜ増配が続くと考えられるか（層ごとの数値と、その根拠になった実数）
    2. 株価と配当利回りの推移（利回りが上がった理由が下落か増配かを見分ける）
    3. いくらで買えば目標利回りに届くか（指値）
    4. 何が起きたら間違いだったと認めるか（棄却条件）
"""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from modules.format import pct, to_pct, yen, yen_short
from modules.store import read_df
from modules.ui import (LAYER_LABELS, get_config, get_dividend_profile, get_prices,
                        get_scores, no_data_guard)
from modules.valuation import price_for_target_yield, yield_percentile, yield_series

st.title("📄 銘柄カルテ")

config = get_config()
scores = get_scores()
if no_data_guard(scores):
    st.stop()

scores = scores.sort_values("total", ascending=False)
labels = scores.apply(lambda r: f"{r['code']} {r['name']}（{r['sector33']}）", axis=1)
options = dict(zip(labels, scores["ticker"]))
ticker_to_label = {v: k for k, v in options.items()}

# 発掘ページから飛んできた場合はその銘柄を開く
pre = st.session_state.get("profile_ticker") or st.query_params.get("ticker")
keys = list(options.keys())
default_idx = keys.index(ticker_to_label[pre]) if pre in ticker_to_label else 0

choice = st.selectbox("銘柄（コードや社名で検索できます）", keys, index=default_idx)
ticker = options[choice]
st.session_state["profile_ticker"] = ticker
row = scores[scores["ticker"] == ticker].iloc[0]

prof = get_dividend_profile(ticker)
prices = get_prices(ticker)
div = read_df("SELECT date, amount FROM dividends WHERE ticker = ? ORDER BY date", (ticker,))
detail = json.loads(row["detail_json"]) if isinstance(row["detail_json"], str) else {}
raw = detail.get("raw", {})
company = read_df("SELECT * FROM company_profile WHERE ticker = ?", (ticker,))
edinet = read_df("SELECT * FROM edinet_summary WHERE ticker = ? ORDER BY fiscal_year", (ticker,))

# ── ヘッダー ──
# 指標を6つ横に並べると、窓が狭いときに値が「¥2,…」と切れて読めなくなる。3つずつ2段にする。
c1, c2, c3 = st.columns(3)
c1.metric("株価", yen(row.get("last_close")))
c2.metric("利回り（実績）", pct(raw.get("dividend_yield"), 2))
c3.metric("連続増配 ／ 10年の減配",
          f"{int(prof['streak'])} 年 ／ {int(prof['cuts_10y'])} 回")

c4, c5, c6 = st.columns(3)
c4.metric("発掘スコア", f"{row['total']:.1f}" if pd.notna(row["total"]) else "—",
          help="市場にまだ気づかれていない増配候補としての点数。大型株は構造的に低く出ます")
c5.metric("配当継続スコア", f"{row['health']:.1f}" if pd.notna(row.get("health")) else "—",
          help="配当が続くか・増えるかだけを見た点数。持っている株の評価はこちら")
hem_h = raw.get("hem_ratio")
c6.metric("ヘム指数", f"{hem_h:.2f}" if hem_h else "—",
          help="配当利回り×10 ÷ 配当性向。1.00以上でヘムの基準を満たす")

if row["gate_passed"] != 1:
    st.error(f"**採用基準を外れている**：{row['gate_reason']}")

# ── 0. 何をやっている会社か ──
st.markdown("#### この会社は何をやっているか")
biz_ja = biz_en = ""
emp = None
if not company.empty:
    biz_ja = str(company["business_ja"].iloc[0] or "").split("【事業の内容】")[-1].strip()
    biz_en = str(company["business_en"].iloc[0] or "").strip()
    emp = company["employees"].iloc[0]

# 大企業の有報は「事業の内容」が他ページへの参照だけのことがある
# （実測: 三菱商事は「連結財務諸表注記1をご参照ください」）。
# 中身が薄いときは英文の会社概要で補う。
ja_is_thin = (not biz_ja) or len(biz_ja) < 60 or "参照" in biz_ja[:80]

if biz_ja:
    st.info(biz_ja[:400] + ("…" if len(biz_ja) > 400 else ""))
    st.caption("出典：有価証券報告書「事業の内容」")
if ja_is_thin and biz_en:
    st.info(biz_en[:400] + ("…" if len(biz_en) > 400 else ""))
    st.caption("出典：yfinance の会社概要（有報の記載が参照のみだったため補足）")
if not biz_ja and not biz_en:
    st.warning(f"事業の内容がまだ取れていません（{row['sector33']}／{row['market']}）。"
               "`uv run python scripts/fetch_edinet.py --fetch` で取得できます。")

meta = [f"業種：{row['sector33']}", f"市場：{row['market']}"]
if pd.notna(emp):
    meta.append(f"従業員：{int(emp):,} 名")
if raw.get("market_cap_oku"):
    meta.append(f"時価総額：{raw['market_cap_oku']:,.0f} 億円")
if not company.empty and pd.notna(company["industry_en"].iloc[0]):
    meta.append(f"業界：{company['industry_en'].iloc[0]}")
st.caption("　／　".join(meta))

st.divider()

# ── 1. なぜ増配が続くと考えられるか ──
st.subheader("1. なぜ増配が続くと考えられるか")
st.caption("各点数は、採用基準を通った約700社の中での順位です。50点が真ん中。")

cols = st.columns(len(LAYER_LABELS) + 1)
for col, (key, label) in zip(cols, LAYER_LABELS.items()):
    col.metric(label, f"{row[key]:.0f}" if pd.notna(row.get(key)) else "—")
penalty = row.get("trap_penalty") or 0
cols[-1].metric("トラップ減点", f"-{penalty:.0f}" if penalty else "なし")

cols = st.columns(len(LAYER_LABELS))
for col, (key, label) in zip(cols, LAYER_LABELS.items()):
    with col:
        st.markdown(f"**{label}**")
        for k, v in (detail.get(key) or {}).items():
            st.caption(f"{k}　**{v:.0f}**" if isinstance(v, (int, float)) else f"{k}　—")

traps_found = detail.get("traps") or []
for t in traps_found:
    st.warning(f"⚠️ {t}")

# ── 配当方針 ──
if not company.empty and company["dividend_policy"].iloc[0]:
    flags = {}
    try:
        flags = json.loads(company["policy_flags"].iloc[0] or "{}")
    except Exception:
        pass
    with st.expander("会社が掲げている配当方針" + (f"（{ '・'.join(flags) }）" if flags else "")):
        if flags:
            st.success("検出された方針：" + "、".join(flags))
        st.write(str(company["dividend_policy"].iloc[0]))
        st.caption("出典：有価証券報告書「配当政策」")

# ── 根拠になった実数 ──
hem = raw.get("hem_ratio")
src = "有価証券報告書" if raw.get("edinet_years") else "yfinance"
facts = pd.DataFrame([
    ("配当性向", pct(raw.get("payout_ratio")), src),
    ("ヘム指数（利回り×10÷配当性向）",
     f"{hem:.2f}　{'✓ 基準を満たす' if hem and hem >= 1.0 else '基準に未達'}" if hem else "—", "計算値"),
    ("FCF配当カバー率", f"{raw.get('fcf_cover'):.1f} 倍" if raw.get("fcf_cover") else "—", "yfinance"),
    ("ネットキャッシュ比率", pct(raw.get("net_cash_ratio")), "yfinance"),
    ("ネット有利子負債 ÷ 営業CF",
     f"{raw.get('net_debt_to_ocf'):.1f} 年" if raw.get("net_debt_to_ocf") is not None else "—", "yfinance"),
    ("自己資本比率", pct(raw.get("equity_ratio")), src),
    ("ROE", pct(raw.get("roe")), src),
    ("EPS 5年成長", pct(raw.get("eps_cagr")), "有価証券報告書"),
    ("DPS 5年成長", pct(raw.get("cagr_5y")), "配当履歴"),
    ("DPS 10年成長", pct(raw.get("cagr_10y")), "配当履歴"),
    ("営業CFの伸び", pct(raw.get("ocf_cagr")), src),
    ("PER / PBR", f"{raw.get('per', 0):.1f} / {raw.get('pbr', 0):.2f}", "yfinance"),
], columns=["項目", "値", "出どころ"])
st.dataframe(facts, hide_index=True, width="stretch", height=460,
             column_config={"項目": st.column_config.TextColumn(width="medium")})

# ── 有報の5年推移 ──
if not edinet.empty:
    basis = edinet["basis"].dropna().iloc[0] if "basis" in edinet.columns and edinet["basis"].notna().any() else "—"
    with st.expander(f"有価証券報告書「主要な経営指標等の推移」（{len(edinet)}年分・{basis}ベース）"):
        show = edinet[["fiscal_year", "sales", "net_income", "eps", "dps",
                       "payout_ratio", "roe", "equity_ratio", "operating_cf", "employees"]].copy()
        for c in ("payout_ratio", "roe", "equity_ratio"):
            show[c] = to_pct(show[c])
        show = show.rename(columns={
            "fiscal_year": "年度", "sales": "売上高", "net_income": "純利益", "eps": "EPS",
            "dps": "1株配当", "payout_ratio": "配当性向", "roe": "ROE",
            "equity_ratio": "自己資本比率", "operating_cf": "営業CF", "employees": "従業員"})
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "売上高": st.column_config.NumberColumn(format="%.0f"),
            "純利益": st.column_config.NumberColumn(format="%.0f"),
            "営業CF": st.column_config.NumberColumn(format="%.0f"),
            "EPS": st.column_config.NumberColumn(format="¥%.2f"),
            "1株配当": st.column_config.NumberColumn(
                format="¥%.2f",
                help="有報に記載されたままの値。**株式分割が調整されていない**ので、"
                     "分割をまたぐと連続しません（増配率の計算には使っていません）"),
            "配当性向": st.column_config.NumberColumn(format="%.1f%%"),
            "ROE": st.column_config.NumberColumn(format="%.1f%%"),
            "自己資本比率": st.column_config.NumberColumn(format="%.1f%%"),
        })
        st.caption(f"金融庁 EDINET に提出された確定値（{basis}ベース）。"
                   "**1株配当は株式分割が調整されていません**（分割をまたぐと連続しません）。"
                   "増配率は分割調整済みの配当履歴から別途計算しています。"
                   "配当性向が空欄なのは、その基準での開示が無い場合です。")

st.divider()

# ── 2. 株価と配当の推移 ──
st.subheader("2. 株価と配当は、どう動いてきたか")
st.caption("利回りが上がった理由が「株価が下がったから」なのか「配当が増えたから」なのかを、"
           "ここで見分けます。**株価が落ちただけの高利回りは、減配の前触れであることが多い**ためです。")

px_series = pd.Series(prices["close"].values, index=pd.to_datetime(prices["date"])).sort_index()
series = pd.Series(prof["series"]).sort_index()
ys = yield_series(prices, div)

fig = make_subplots(specs=[[{"secondary_y": True}]])
if not px_series.empty:
    cutoff = px_series.index.max() - pd.DateOffset(years=10)
    p10 = px_series[px_series.index >= cutoff]
    fig.add_scatter(x=p10.index, y=p10.values, name="株価", mode="lines",
                    line=dict(color="#4C8BF5", width=2), secondary_y=False)
if not ys.empty:
    y10 = ys[(ys.index >= ys.index.max() - pd.DateOffset(years=10)) & (ys > 0)]
    fig.add_scatter(x=y10.index, y=y10.values * 100, name="配当利回り", mode="lines",
                    line=dict(color="#E45756", width=1.5, dash="dot"), secondary_y=True)
fig.update_yaxes(title_text="株価（円）", secondary_y=False)
fig.update_yaxes(title_text="配当利回り（%）", secondary_y=True, showgrid=False)
fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10),
                  legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig, width="stretch")

if not series.empty:
    fig2 = go.Figure(go.Bar(x=series.index.astype(str), y=series.values, marker_color="#4C8BF5"))
    fig2.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10),
                       yaxis_title="1株配当（円）", xaxis_title="配当年度")
    st.plotly_chart(fig2, width="stretch")
    if prof["has_spike"]:
        st.caption("※ 記念配当・特別配当と思われる年を検出しています。"
                   "連続増配・減配回数の判定からは除外しています。")

st.divider()

# ── 3. 指値 ──
st.subheader("3. いくらで買えば目標利回りに届くか")
info = yield_percentile(prices, div, config["scoring"]["yield_percentile_window_years"])
hold = read_df("SELECT target_yield FROM holdings WHERE ticker = ?", (ticker,))
watch = read_df("SELECT target_yield, thesis, invalidation FROM watchlist WHERE ticker = ?", (ticker,))
src_t = hold if not hold.empty else watch
default_target = (float(src_t["target_yield"].iloc[0])
                  if not src_t.empty and pd.notna(src_t["target_yield"].iloc[0]) else 0.047)

c1, c2 = st.columns([1, 1.4])
with c1:
    target = st.number_input("目標利回り（%）", 1.0, 12.0, default_target * 100, 0.1) / 100
    dps = prof["dps_latest"]
    limit_price = price_for_target_yield(dps, target)
    now_price = row.get("last_close")
    st.metric(f"利回り {target:.1%} に届く株価", yen(limit_price))
    if limit_price and now_price:
        gap = limit_price / now_price - 1
        if gap >= 0:
            st.success(f"**すでに目標を超えています**（現在 {raw.get('dividend_yield', 0):.2%}）。\n\n"
                       f"¥{limit_price:,.0f} までなら買っても目標に届きます。")
        else:
            st.info(f"**まだ目標に届いていません**（現在 {raw.get('dividend_yield', 0):.2%}）。\n\n"
                    f"¥{limit_price:,.0f} まで {-gap:.1%} 下がるのを待ちます。")
    if info.get("price_at_median_yield"):
        st.metric("いつもの水準に戻った場合の株価", yen(info["price_at_median_yield"]),
                  help="いまの配当のまま、利回りが過去7年の中央値まで下がった場合の株価。"
                       "狙う目標ではなく、上値の目安として見ます")

with c2:
    if info.get("percentile") is not None:
        st.markdown(f"#### 自己利回り順位　**{info['percentile']:.0%}**")
        st.progress(float(info["percentile"]))
        st.caption(
            f"この銘柄の過去{config['scoring']['yield_percentile_window_years']}年の"
            f"利回り分布の中で、いまの {info['current']:.2%} は **上位 {info['percentile']:.0%}** "
            f"の位置です。100%に近いほど「この会社としては、めったにない高利回り」。\n\n"
            f"過去の中央値 **{pct(info.get('median'), 2)}** ／ "
            f"よくある範囲 {pct(info.get('p25'), 2)}〜{pct(info.get('p75'), 2)}")
    ex = raw.get("ex_dividend_date")
    if not company.empty and pd.notna(company["ex_dividend_date"].iloc[0]):
        st.metric("直近の権利確定日", str(company["ex_dividend_date"].iloc[0]),
                  help="この日までに買って持っていれば配当を受け取れます")
    if not company.empty and pd.notna(company["dividend_rate"].iloc[0]):
        st.metric("会社予想の年間配当", yen(company["dividend_rate"].iloc[0]))

st.divider()

# ── 4. 棄却条件 ──
st.subheader("4. 何が起きたら間違いだったと認めるか")
st.caption("ここに書いた条件は 🚨 監視 が自動で見張ります。"
           "買う前に「どうなったら降りるか」を決めておくためのものです。")

auto = []
payout = raw.get("payout_ratio")
if payout:
    auto.append(f"配当性向が {min(payout + 0.20, 0.90):.0%} を超えたら、増配の原資が尽きかけている")
auto.append("純利益または営業CFが2期連続で減少したら、増配ストーリーの前提が崩れる")
auto.append("減配または無配転落が発表されたら、その時点で仮説は棄却")
if raw.get("net_debt_to_ocf") is not None:
    auto.append(f"ネット有利子負債 ÷ 営業CF が {raw['net_debt_to_ocf'] + 3:.0f} 年を超えたら、"
                "財務が配当を支えられない")

existing_inv = watch["invalidation"].iloc[0] if not watch.empty else None
existing_th = watch["thesis"].iloc[0] if not watch.empty else None
c1, c2 = st.columns(2)
with c1:
    text = st.text_area("棄却条件", value=existing_inv or "\n".join(f"・{a}" for a in auto),
                        height=170)
with c2:
    thesis = st.text_area("投資仮説（市場が見落としている理由）", value=existing_th or "",
                          height=170,
                          placeholder="例：アナリストがカバーしておらず、PBR1倍割れのまま増益が続いている")

if st.button("ウォッチリストに保存", type="primary"):
    from modules.store import connect
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist "
            "(ticker, name, target_yield, bottom_yield, source, thesis, invalidation, added_at, note) "
            "VALUES (?, ?, ?, (SELECT bottom_yield FROM watchlist WHERE ticker=?), "
            "'scout', ?, ?, COALESCE((SELECT added_at FROM watchlist WHERE ticker=?), date('now')), "
            "(SELECT note FROM watchlist WHERE ticker=?))",
            (ticker, row["name"], target, ticker, thesis, text, ticker, ticker),
        )
    st.success(f"{row['name']} をウォッチリストに保存しました。監視タブで見張ります。")
    st.cache_data.clear()
