"""資金投入 — 「トレード利益を移した。どこに入れるか」に答える。

3つに分かれる。
    相場と現金  いくら入れて、いくら残すか（ヘムの3本目の柱）
    買う        どの銘柄に配るか・買ったら記録する
    整理する    どれから手をつけるか・判断を覚える・売ったら記録する

【発掘スコア順に買わない】
発掘スコアは「市場にまだ気づかれていないか」を含むので、見つける順位であって
買う順位ではない。買い付け優先度は √(割安度 × 配当継続) で別に出す。
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import market, review as R
from modules.allocator import allocate, buy_gate, buy_priority, month_gaps, rebalance_funds
from modules.format import to_pct, yen, yen_short
from modules.portfolio import dividend_calendar, review_candidates, sector_exposure
from modules.store import connect, read_df
from modules.ui import get_config, get_positions, get_scores, no_data_guard

st.title("💰 資金投入")

config = get_config()
scores = get_scores()
if no_data_guard(scores):
    st.stop()

positions = get_positions(config)
raw_review = review_candidates(positions, config)
review = R.sell_priority(R.attach(raw_review), config) if not raw_review.empty else raw_review
calendar = dividend_calendar(positions)
gaps = month_gaps(positions, calendar)
total_eval = positions["eval_value"].sum() if not positions.empty else 0.0


@st.cache_data(ttl=3600, show_spinner="相場の水準を確認中...")
def _market() -> dict:
    pbr = market.fetch_nikkei_pbr()
    price = market.fetch_nikkei_price()
    snap = market.snapshot(price, pbr)
    if snap:
        market.record_snapshot(snap, pbr)
    return snap or market.load_snapshot()


snap = _market()
regime_name, deploy_ratio, regime_note = market.regime(snap, config)

tab_market, tab_buy, tab_sell = st.tabs(["📉 相場と現金", "🛒 買う", "🧹 整理する（売る）"])

# ═══════════════════════════════════════════════ 相場と現金
with tab_market:
    st.caption("ヘムの3本目の柱「暴落時の買い向かい」。"
               "現金を残しておき、下がるほど多く入れるための材料です。")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("日経平均", f"{snap.get('nikkei', 0):,.0f} 円" if snap.get("nikkei") else "—")
    c2.metric("日経平均PBR（加重平均）", f"{snap.get('pbr', 0):.2f} 倍" if snap.get("pbr") else "—",
              help="株価が1株純資産の何倍か。リーマン・ショックのときは0.8倍まで下がりました")
    c3.metric("過去10年での位置",
              f"上位 {1 - snap['pct_10y']:.0%}" if snap.get("pct_10y") is not None else "—",
              help="日経平均が、過去10年の分布の中でどのあたりか")
    c4.metric("200日移動平均との差",
              f"{snap['vs_ma200']:+.1%}" if snap.get("vs_ma200") is not None else "—")

    color = {"総力戦": "error", "大人買い": "error", "買い増し": "success",
             "平常": "info", "やや高い": "warning", "高い": "warning"}.get(regime_name, "info")
    getattr(st, color)(f"### 【{regime_name}】入金額の {deploy_ratio:.0%} を投入\n\n{regime_note}")

    st.markdown("#### 大人買いライン")
    reserve_pct = st.slider("総資産のうち、暴落用に取っておく現金の割合", 0, 60,
                            int(config["market"]["cash_reserve"] * 100), 5, format="%d%%") / 100
    reserve = total_eval * reserve_pct / (1 - reserve_pct) if reserve_pct < 1 else 0
    st.caption(f"いまの評価額 {yen_short(total_eval)} に対して、"
               f"**{yen_short(reserve)}** を現金で持っておく計算です。")

    ladder = market.buy_ladder(snap, config, reserve)
    if ladder["日経平均の水準"].notna().any():
        show = ladder.copy()
        show["日経平均の水準"] = show["日経平均の水準"].round(0)
        show["いまからの下落率"] = to_pct(show["いまからの下落率"]).round(1)
        show["投入する割合"] = to_pct(show["投入する割合"]).round(0)
        show["投入額"] = show["投入額"].round(0)
        show["到達済み"] = show["到達済み"].map({True: "✅ 到達", False: ""})
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "日経平均PBR": st.column_config.NumberColumn(format="%.2f 倍"),
            "日経平均の水準": st.column_config.NumberColumn(
                format="%d 円", help="この日経平均になったら、その行の金額を投入する"),
            "いまからの下落率": st.column_config.NumberColumn(format="%.1f%%"),
            "投入する割合": st.column_config.NumberColumn(
                format="%.0f%%", help="取ってある現金のうち、その水準で入れる割合"),
            "投入額": st.column_config.NumberColumn(format="¥%d"),
        })
        st.caption("PBRは「株価 ÷ 1株純資産」なので、1株純資産が変わらないとすれば "
                   "**目標PBRに対応する日経平均 = いまの日経平均 × 目標PBR ÷ いまのPBR** "
                   "で逆算できます。証券会社のアプリでこの価格にアラートを仕掛けておくと、"
                   "暴落のときに迷わず動けます。")

        fig = go.Figure()
        lv = ladder.dropna(subset=["日経平均の水準"])
        fig.add_bar(x=[f"PBR {p:.2f}" for p in lv["日経平均PBR"]],
                    y=lv["日経平均の水準"], marker_color="#4C8BF5",
                    text=[f"{v:,.0f}円" for v in lv["日経平均の水準"]], textposition="outside")
        if snap.get("nikkei"):
            fig.add_hline(y=snap["nikkei"], line_dash="dash", line_color="#E45756",
                          annotation_text=f"いま {snap['nikkei']:,.0f}円")
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                          yaxis_title="日経平均（円）")
        st.plotly_chart(fig, width="stretch")
    else:
        st.warning("日経平均PBRが取れていないため、大人買いラインを出せません。")

    hist = read_df("SELECT * FROM market_history ORDER BY date")
    if len(hist) > 3:
        with st.expander(f"貯まっている相場の記録（{len(hist)} 日ぶん）"):
            st.caption("日経のサイトは当月ぶんしか返さないため、"
                       "スキャンのたびに少しずつ貯めて自前の履歴を作っています。")
            st.dataframe(hist.tail(60), hide_index=True, width="stretch")

# ═══════════════════════════════════════════════ 買う
with tab_buy:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cash_in = st.number_input("入金額（円）", 0, 100_000_000, 500_000, 50_000, format="%d")
        account = st.radio("入れる口座", ["specific", "nisa"], index=0,
                           format_func=lambda a: "特定口座（課税 20.315%）" if a == "specific"
                           else "NISA（非課税）",
                           help="NISAは配当が非課税。同じ配当でも手取りが約2割変わります")
    with c2:
        use_regime = st.checkbox(
            f"相場の水準に合わせる（いま【{regime_name}】＝{deploy_ratio:.0%}）", value=True,
            help="高い局面では入金を使い切らず、暴落に備えて現金に積みます")
        max_names = st.number_input(
            "買う銘柄数の上限", 1, 40, 12,
            help="ヘムは360〜400銘柄に超分散。機械的な基準で選び監視はアプリがやるので、"
                 "絞る理由は薄いです")
    with c3:
        min_yield = st.number_input("最低利回り（%）", 0.0, 8.0, 3.0, 0.25) / 100
        per_cap = st.slider("1銘柄あたりの上限（投入額に対する比率）", 5, 100, 15, 5,
                            format="%d%%") / 100
    with c4:
        scope = st.radio("対象", ["未保有のみ", "保有の買い増しも含む"], index=1)
        require_target = st.checkbox("目標利回りに届いている銘柄だけ", value=False,
                                     help="届いていないなら待つ、というヘムの型")

    cash = cash_in * (deploy_ratio if use_regime else 1.0)
    if use_regime and deploy_ratio < 1.0:
        st.info(f"【{regime_name}】のため、入金 {yen(cash_in)} のうち **{yen(cash)}** を投入し、"
                f"**{yen(cash_in - cash)}** は暴落用の現金に積みます。")

    held = set(positions["ticker"]) if not positions.empty else set()
    cand = scores[scores["gate_passed"] == 1].copy().reset_index(drop=True)
    raw = pd.DataFrame([json.loads(x)["raw"] for x in cand["detail_json"]])
    cand = pd.concat([cand, raw[["dividend_yield", "yield_percentile", "streak",
                                 "payout_months"]]], axis=1)
    if scope == "未保有のみ":
        cand = cand[~cand["ticker"].isin(held)]
    cand = cand[cand["dividend_yield"].fillna(0) >= min_yield]
    targets = read_df("SELECT ticker, target_yield FROM watchlist "
                      "UNION SELECT ticker, target_yield FROM holdings").groupby("ticker").first()
    cand = cand.join(targets, on="ticker")

    scored = buy_priority(cand, positions, config, gaps)
    _, gate_reasons = buy_gate(scored, config)
    n_cut = int((gate_reasons != "").sum())
    if n_cut:
        st.caption(f"候補 {len(scored)} 件のうち **{n_cut} 件を足切り**しました"
                   f"（{gate_reasons[gate_reasons != ''].value_counts().to_dict()}）。"
                   "安くても、配当が危ない銘柄と高配当トラップの判定が出ている銘柄には入れません。")

    plan = allocate(cash, cand, positions, config, max_names=int(max_names),
                    per_name_cap_pct=float(per_cap), require_below_target=require_target,
                    month_gap=gaps)

    st.divider()
    if plan.empty:
        st.warning("条件に合う配分先がありません。銘柄数の上限を増やすか、利回りの下限を下げてください。")
    else:
        tax = config["portfolio"]["tax_rate_nisa"] if account == "nisa" \
            else config["portfolio"]["tax_rate_specific"]
        total_in, total_div = plan["投入額"].sum(), plan["年間配当"].sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("投入額", yen(total_in))
        c2.metric("増える年間配当（税引前）", yen(total_div))
        c3.metric("増える年間配当（税引後）", yen(total_div * (1 - tax)))
        c4.metric("現金に積む", yen(cash_in - total_in))

        show = plan[["コード", "銘柄名", "業種", "配当月", "株価", "株数", "投入額", "利回り",
                     "年間配当", "買い付け優先度", "割安度", "自己利回り順位", "絶対利回り順位",
                     "継続", "補完度", "発掘スコア"]].copy()
        show["利回り"] = to_pct(show["利回り"])
        show["税引後配当"] = (show["年間配当"] * (1 - tax)).round(0)
        for c in ("買い付け優先度", "割安度", "自己利回り順位", "絶対利回り順位",
                  "継続", "補完度", "発掘スコア"):
            show[c] = show[c].round(0)
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "株価": st.column_config.NumberColumn(format="¥%d"),
            "投入額": st.column_config.NumberColumn(format="¥%d"),
            "年間配当": st.column_config.NumberColumn(format="¥%d"),
            "税引後配当": st.column_config.NumberColumn(format="¥%d"),
            "利回り": st.column_config.NumberColumn(format="%.2f%%"),
            "買い付け優先度": st.column_config.ProgressColumn(
                format="%.0f", min_value=0, max_value=100,
                help="√(割安度 × 配当継続) × 補完度係数 − トラップ減点。発掘スコアとは別物"),
            "割安度": st.column_config.NumberColumn(
                format="%.0f", help="自己利回り順位60% + 候補内の絶対利回り順位40%"),
            "継続": st.column_config.NumberColumn(format="%.0f", help="配当継続スコア"),
            "補完度": st.column_config.NumberColumn(
                format="%.0f", help="空いている業種・空いている配当月を埋めるか（±10%のタイブレーク）"),
            "発掘スコア": st.column_config.NumberColumn(
                format="%.0f", help="参考表示。買う順位の決定には使っていません"),
        })
        st.caption("株数は単元（100株）に丸めています。**買い付け優先度の順に**、"
                   "業種の上限と1銘柄あたりの上限を守りながら埋めています。")

        with st.expander("なぜこの式なのか"):
            st.markdown("""
#### 発掘スコア順に買わない理由

発掘スコアには「見過ごされ度」が重み1.2で入っています。
**市場に気づかれていないことは、見つける理由にはなっても、いま買う理由になりません。**

#### 足し算ではなく掛け算にした理由

    買い付け優先度 = √(割安度 × 配当継続) × 補完度係数 − トラップ減点

加重和だと「片方が極端に高ければ、もう片方が低くても選ばれて」しまいます。
実測すると『割安度80超・配当継続35未満』の銘柄が13件あり、加重和では69点で
上位に入っていました（小松マテーレ 割安95・継続34 など）。

配当投資では「安いが配当が危ない」も「安全だが高い」も買いたくない。
**両方そろって初めて買う**という性質を、そのまま式にしています。

| | 加重和 | 幾何平均 |
|---|---|---|
| 割安100・継続100 | 100 | **100** |
| 割安100・継続25 | 68 | **50** |
| 割安60・継続60 | 60 | **60** |

#### 割安度に2つ混ぜている理由

「安い」には2つの意味があります。

- **自己利回り順位**（60%）… その銘柄自身の過去と比べて安いか＝**買う時期**の判断
- **絶対利回り順位**（40%）… 候補の中で利回りが高いか＝**いま受け取れる額**

自己利回り順位だけだと「いつも1.5%の銘柄が2.0%」を最高評価してしまい、
インカムとしては物足りない銘柄が上位に来ます。

#### 足切り

順位を付ける前に外します。**安いからといって、配当が危ない銘柄や
高配当トラップの判定が出ている銘柄に資金を入れる理由はありません。**

- 配当継続スコア 40未満
- 高配当トラップの減点 10以上
""")

        st.markdown("#### 買ったら記録する")
        st.caption("実際に発注して約定したら、ここで記録すると保有に反映されます。")
        edited = st.data_editor(
            pd.DataFrame({"記録する": True, "コード": plan["コード"].values,
                          "銘柄名": plan["銘柄名"].values, "株数": plan["株数"].values,
                          "約定単価": plan["株価"].round(1).values}),
            hide_index=True, width="stretch", key="buy_editor",
            column_config={
                "記録する": st.column_config.CheckboxColumn(),
                "コード": st.column_config.TextColumn(disabled=True),
                "銘柄名": st.column_config.TextColumn(disabled=True),
                "株数": st.column_config.NumberColumn(min_value=0, step=100),
                "約定単価": st.column_config.NumberColumn(format="¥%.1f", min_value=0.0)})
        buy_date = st.date_input("約定日", value=date.today(), key="buy_date")
        if st.button("この内容で買ったことにする", type="primary"):
            rows = edited[edited["記録する"] & (edited["株数"] > 0)]
            if rows.empty:
                st.warning("記録する行がありません")
            else:
                with connect() as conn:
                    for r in rows.itertuples(index=False):
                        ticker = f"{r.コード}.T"
                        shares, price = float(r.株数), float(r.約定単価)
                        conn.execute(
                            "INSERT INTO transactions (date, account, ticker, name, type, "
                            "shares, price, fee, memo) VALUES (?, ?, ?, ?, 'buy', ?, ?, 0, ?)",
                            (buy_date.isoformat(), account, ticker, r.銘柄名, shares, price,
                             "資金投入画面から記録"))
                        cur = conn.execute(
                            "SELECT shares, avg_cost FROM holdings WHERE account=? AND ticker=?",
                            (account, ticker)).fetchone()
                        if cur and cur["shares"]:
                            ns = float(cur["shares"]) + shares
                            nc = (float(cur["shares"]) * float(cur["avg_cost"] or 0)
                                  + shares * price) / ns
                        else:
                            ns, nc = shares, price
                        conn.execute(
                            "INSERT OR REPLACE INTO holdings (account, ticker, name, shares, "
                            "avg_cost, target_yield, bottom_yield, updated_at) VALUES (?, ?, ?, ?, ?,"
                            " COALESCE((SELECT target_yield FROM holdings WHERE account=? AND ticker=?), 0.047),"
                            " (SELECT bottom_yield FROM holdings WHERE account=? AND ticker=?),"
                            " datetime('now'))",
                            (account, ticker, r.銘柄名, ns, nc, account, ticker, account, ticker))
                st.success(f"{len(rows)} 銘柄を記録しました。ポートフォリオに反映されています。")
                st.cache_data.clear()

# ═══════════════════════════════════════════════ 整理する
with tab_sell:
    if review.empty:
        st.success("整理を検討すべき保有はありません")
    else:
        undecided = review[review["判断"] == ""]
        n_big = int((review["重さ"] == "重大").sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🔴 配当が危ない", n_big)
        c2.metric("🟡 額が小さいだけ", int((review["重さ"] == "軽微").sum()))
        c3.metric("未判断", len(undecided), help="まだ「持ち続ける／様子見」を決めていないもの")
        c4.metric("判断済み", len(review) - len(undecided))

        st.caption("**整理の優先度**は「配当の危なさ50% + 保有額の大きさ30% + 含み損の大きさ20%」。"
                   "含み損の銘柄を先に売れば、特定口座では譲渡益と相殺できて税金が軽くなります。"
                   "判断を記録すると、次からは既定では隠れます。")

        c1, c2 = st.columns([1, 1])
        with c1:
            pick_sev = st.multiselect("重さ", ["重大", "軽微"], default=["重大"])
        with c2:
            hide_decided = st.checkbox("判断済みを隠す", value=True)

        shown = review[review["重さ"].isin(pick_sev)] if pick_sev else review
        if hide_decided:
            shown = shown[shown["判断"] == ""]

        if shown.empty:
            st.success("この条件に該当する未判断の保有はありません。"
                       "「判断済みを隠す」を外すと、決めたものも見られます。")
        else:
            tax_rate = config["portfolio"]["tax_rate_specific"]
            gain = (shown["last_close"] - shown["avg_cost"].fillna(0)) * shown["shares"]
            tax_est = np.where(shown["account"] == "nisa", 0.0,
                               np.maximum(gain, 0) * tax_rate)
            view = pd.DataFrame({
                "整理の優先度": shown["整理の優先度"].values,
                "重さ": shown["重さ"].values,
                "コード": shown["code"].values,
                "銘柄名": shown["name"].values,
                "業種": shown["sector33"].values,
                "口座": shown["account"].map({"specific": "特定", "nisa": "NISA"}).values,
                "株数": shown["shares"].values,
                "株価": shown["last_close"].values,
                "評価額": shown["eval_value"].values,
                "損益率": to_pct(shown["pnl_pct"]).values,
                "売却益の税金": np.round(tax_est, 0),
                "配当継続": shown["health"].round(0).values,
                "理由": shown["整理を検討する理由"].values,
                "判断": shown["判断"].values,
            })
            st.dataframe(view, hide_index=True, width="stretch", height=340, column_config={
                "整理の優先度": st.column_config.ProgressColumn(
                    format="%.0f", min_value=0, max_value=100),
                "株価": st.column_config.NumberColumn(format="¥%d"),
                "評価額": st.column_config.NumberColumn(format="¥%d"),
                "損益率": st.column_config.NumberColumn(format="%.1f%%"),
                "売却益の税金": st.column_config.NumberColumn(
                    format="¥%d", help="いま売った場合にかかる税金の見込み。NISAは非課税"),
                "配当継続": st.column_config.NumberColumn(format="%.0f"),
                "理由": st.column_config.TextColumn(width="large"),
            })

            st.markdown("#### 決めたことを記録する")
            st.caption("「持ち続ける」「様子見」にすると次から隠れます。"
                       "「売った」は株数と約定単価を入れて記録すると保有から減ります。")
            editor = st.data_editor(
                pd.DataFrame({
                    "判断": [""] * len(shown),
                    "コード": shown["code"].values,
                    "銘柄名": shown["name"].values,
                    "口座": shown["account"].map({"specific": "特定", "nisa": "NISA"}).values,
                    "株数": shown["shares"].values,
                    "約定単価": shown["last_close"].round(1).values,
                }),
                hide_index=True, width="stretch", key="sell_editor", height=300,
                column_config={
                    "判断": st.column_config.SelectboxColumn(
                        options=["", "持ち続ける", "様子見", "売った"], required=False),
                    "コード": st.column_config.TextColumn(disabled=True),
                    "銘柄名": st.column_config.TextColumn(disabled=True),
                    "口座": st.column_config.TextColumn(disabled=True),
                    "株数": st.column_config.NumberColumn(min_value=0, step=100),
                    "約定単価": st.column_config.NumberColumn(format="¥%.1f", min_value=0.0)})
            sell_date = st.date_input("約定日（売った場合）", value=date.today(), key="sell_date")

            sold = editor[editor["判断"] == "売った"]
            if not sold.empty:
                st.info(f"**{len(sold)} 銘柄・{yen((sold['株数'] * sold['約定単価']).sum())}** "
                        "を売却として記録します")
            if st.button("この内容で記録する", type="primary"):
                acted = editor[editor["判断"] != ""]
                if acted.empty:
                    st.warning("判断が選ばれている行がありません")
                else:
                    n_keep = n_sold = 0
                    proceeds = 0.0
                    with connect() as conn:
                        for r in acted.itertuples(index=False):
                            ticker = f"{r.コード}.T"
                            acc = "nisa" if r.口座 == "NISA" else "specific"
                            if r.判断 in ("持ち続ける", "様子見"):
                                R.save(acc, ticker,
                                       "keep" if r.判断 == "持ち続ける" else "watch")
                                n_keep += 1
                                continue
                            shares, price = float(r.株数), float(r.約定単価)
                            if shares <= 0:
                                continue
                            conn.execute(
                                "INSERT INTO transactions (date, account, ticker, name, type, "
                                "shares, price, fee, memo) VALUES (?, ?, ?, ?, 'sell', ?, ?, 0, ?)",
                                (sell_date.isoformat(), acc, ticker, r.銘柄名, shares, price,
                                 "整理タブから記録"))
                            cur = conn.execute(
                                "SELECT shares FROM holdings WHERE account=? AND ticker=?",
                                (acc, ticker)).fetchone()
                            left = (float(cur["shares"]) if cur else 0.0) - shares
                            if left <= 0.5:
                                conn.execute("DELETE FROM holdings WHERE account=? AND ticker=?",
                                             (acc, ticker))
                                conn.execute("DELETE FROM holding_review WHERE account=? AND ticker=?",
                                             (acc, ticker))
                            else:
                                conn.execute("UPDATE holdings SET shares=?, "
                                             "updated_at=datetime('now') WHERE account=? AND ticker=?",
                                             (left, acc, ticker))
                            n_sold += 1
                            proceeds += shares * price
                    msg = []
                    if n_keep:
                        msg.append(f"{n_keep} 銘柄の判断を記録しました（次から隠れます）")
                    if n_sold:
                        msg.append(f"{n_sold} 銘柄の売却を記録しました。売却代金 {yen(proceeds)}")
                    st.success("／".join(msg))
                    st.cache_data.clear()

        with st.expander("判断を取り消す"):
            dec = R.load()
            if dec.empty:
                st.caption("記録された判断はまだありません")
            else:
                names = read_df("SELECT ticker, name FROM universe").set_index("ticker")["name"]
                dec["銘柄名"] = dec["ticker"].map(names)
                dec["判断"] = dec["decision"].map(R.DECISIONS)
                st.dataframe(dec[["account", "ticker", "銘柄名", "判断", "decided_at"]].rename(
                    columns={"account": "口座", "ticker": "銘柄", "decided_at": "判断日"}),
                    hide_index=True, width="stretch")
                undo = st.selectbox("取り消す銘柄", [""] + [
                    f"{r.account}／{r.ticker} {r.銘柄名}" for r in dec.itertuples(index=False)])
                if undo and st.button("この判断を取り消す"):
                    acc, rest = undo.split("／")
                    R.clear(acc, rest.split(" ")[0])
                    st.success("取り消しました")
                    st.cache_data.clear()

    st.divider()
    with st.expander("売買の記録"):
        tx = read_df("SELECT date, account, ticker, name, type, shares, price, memo "
                     "FROM transactions ORDER BY date DESC, id DESC LIMIT 200")
        if tx.empty:
            st.caption("まだ記録がありません")
        else:
            tx["account"] = tx["account"].map({"specific": "特定", "nisa": "NISA"}).fillna(tx["account"])
            tx["type"] = tx["type"].map({"buy": "買い", "sell": "売り", "dividend": "配当"}).fillna(tx["type"])
            tx["金額"] = (tx["shares"] * tx["price"]).round(0)
            st.dataframe(tx.rename(columns={
                "date": "日付", "account": "口座", "ticker": "銘柄", "name": "銘柄名",
                "type": "種別", "shares": "株数", "price": "単価", "memo": "メモ"}),
                hide_index=True, width="stretch", column_config={
                    "単価": st.column_config.NumberColumn(format="¥%.1f"),
                    "金額": st.column_config.NumberColumn(format="¥%d")})
