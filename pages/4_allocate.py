"""資金投入 — 「トレード利益を移した。どこに入れるか」に答える。

配分案を出すだけでなく、**買った／売ったを記録できる**ところまでを1画面で完結させる。
記録できないと、翌月には保有と画面がずれて使えなくなる。

【発掘スコア順に買わない】
発掘スコアは「市場にまだ気づかれていないか」を含むので、見つける順位であって
買う順位ではない。買い付け優先度は 割安度・配当継続・目標到達・補完度 で別に出す。
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from modules.allocator import allocate, month_gaps, rebalance_funds
from modules.format import to_pct, yen, yen_short
from modules.portfolio import dividend_calendar, review_candidates, sector_exposure
from modules.store import connect, read_df
from modules.ui import get_config, get_positions, get_scores, no_data_guard

st.title("💰 資金投入")
st.caption("入金額を、割安さ・配当の確からしさ・ポートフォリオの空きに合わせて割り振る")

config = get_config()
scores = get_scores()
if no_data_guard(scores):
    st.stop()

positions = get_positions(config)
review = review_candidates(positions, config)
calendar = dividend_calendar(positions)
gaps = month_gaps(positions, calendar)

tab_buy, tab_sell = st.tabs(["🛒 買う", "🧹 整理する（売る）"])

# ═══════════════════════════════════════════════════ 買う
with tab_buy:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cash = st.number_input("入金額（円）", 0, 100_000_000, 500_000, 50_000, format="%d")
        account = st.radio("入れる口座", ["specific", "nisa"], index=0,
                           format_func=lambda a: "特定口座（課税 20.315%）" if a == "specific"
                           else "NISA（非課税）",
                           help="NISAは配当が非課税です。同じ配当でも手取りが約2割変わります")
    with c2:
        max_names = st.number_input(
            "買う銘柄数の上限", 1, 40, 12,
            help="ヘムは360〜400銘柄に超分散しています。機械的な基準で選び、"
                 "監視はアプリがやるので、絞る理由は薄いです")
        min_yield = st.number_input("最低利回り（%）", 0.0, 8.0, 3.0, 0.25,
                                    help="これを下回る銘柄には資金を入れません") / 100
    with c3:
        per_cap = st.slider("1銘柄あたりの上限（入金額に対する比率）", 5, 100, 15, 5,
                            format="%d%%") / 100
        scope = st.radio("対象", ["未保有のみ", "保有の買い増しも含む"], index=1)
    with c4:
        require_target = st.checkbox(
            "目標利回りに届いている銘柄だけに絞る", value=False,
            help="届いていないなら待つ、という考え方。ヘムはこの型です")
        sell_funds = rebalance_funds(review[review["重さ"] == "重大"]) if not review.empty else 0.0
        use_sell = False
        if sell_funds > 0:
            use_sell = st.checkbox(
                f"整理候補（重大）{int((review['重さ'] == '重大').sum())} 銘柄"
                f"（{yen_short(sell_funds)}）を売った前提で計算",
                value=False, help="実際に売ったら「整理する」タブで記録してください")

    if use_sell:
        cash += sell_funds

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

    plan = allocate(cash, cand, positions, config, max_names=int(max_names),
                    per_name_cap_pct=float(per_cap), require_below_target=require_target,
                    month_gap=gaps)

    st.divider()
    if plan.empty:
        st.warning("条件に合う配分先がありません。銘柄数の上限を増やすか、利回りの下限を下げてください。")
    else:
        tax = config["portfolio"]["tax_rate_nisa"] if account == "nisa" \
            else config["portfolio"]["tax_rate_specific"]
        total_in = plan["投入額"].sum()
        total_div = plan["年間配当"].sum()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("投入額", yen(total_in))
        c2.metric("増える年間配当（税引前）", yen(total_div))
        c3.metric("増える年間配当（税引後）", yen(total_div * (1 - tax)),
                  help="NISAなら非課税、特定口座なら20.315%が引かれます")
        c4.metric("残り", yen(cash - total_in))

        show = plan[["コード", "銘柄名", "業種", "配当月", "株価", "株数", "投入額", "利回り",
                     "年間配当", "買い付け優先度", "割安度", "継続", "目標到達", "補完度",
                     "発掘スコア", "業種の空き枠"]].copy()
        show["利回り"] = to_pct(show["利回り"])
        show["税引後配当"] = (show["年間配当"] * (1 - tax)).round(0)
        show["業種の空き枠"] = (show["業種の空き枠"] / 1e4).round(0)
        for c in ("買い付け優先度", "割安度", "継続", "目標到達", "補完度", "発掘スコア"):
            show[c] = show[c].round(0)
        show = show[["コード", "銘柄名", "業種", "配当月", "株価", "株数", "投入額", "利回り",
                     "年間配当", "税引後配当", "買い付け優先度", "割安度", "継続", "目標到達",
                     "補完度", "発掘スコア", "業種の空き枠"]]

        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "株価": st.column_config.NumberColumn(format="¥%d"),
            "投入額": st.column_config.NumberColumn(format="¥%d"),
            "年間配当": st.column_config.NumberColumn(format="¥%d"),
            "税引後配当": st.column_config.NumberColumn(format="¥%d"),
            "利回り": st.column_config.NumberColumn(format="%.2f%%"),
            "買い付け優先度": st.column_config.ProgressColumn(
                format="%.0f", min_value=0, max_value=100,
                help="割安度40% + 配当継続30% + 目標到達20% + 補完度10% の合成。"
                     "発掘スコアとは別物です"),
            "割安度": st.column_config.NumberColumn(format="%.0f", help="自己利回り順位"),
            "継続": st.column_config.NumberColumn(format="%.0f", help="配当継続スコア"),
            "目標到達": st.column_config.NumberColumn(
                format="%.0f", help="現在利回り ÷ 目標利回り。100なら届いている"),
            "補完度": st.column_config.NumberColumn(
                format="%.0f", help="空いている業種・空いている配当月を埋めるか"),
            "発掘スコア": st.column_config.NumberColumn(
                format="%.0f", help="参考表示。買う順位の決定には使っていません"),
            "業種の空き枠": st.column_config.NumberColumn(format="%d 万円"),
        })
        st.caption("株数は単元（100株）に丸めています。"
                   "**買い付け優先度の順に**、業種の上限と1銘柄あたりの上限を守りながら埋めています。")

        with st.expander("なぜ発掘スコア順に買わないのか"):
            st.markdown("""
発掘スコアには「見過ごされ度」が重み1.2で入っています。
**市場に気づかれていないことは、見つける理由にはなっても、いま買う理由にはなりません。**

限られた資金の配り先は、別の4つで決めています。

| 要素 | 重み | 理由 |
|---|---|---|
| **割安度**（自己利回り順位） | 40% | 検証で唯一きれいに効いた層。同じ銘柄でも「いつ買うか」で結果が変わる |
| **配当継続スコア** | 30% | 買ったあと減配したら全部台無しになる |
| **目標到達度** | 20% | 目標利回りに届いていないなら待つ、というのがヘムの型 |
| **補完度** | 10% | 空いている業種・空いている配当月を埋める銘柄は、同じ点数でも価値が高い |

重みは `config.yaml` の `buy_priority` で変えられます。
""")

        # ── 買付の記録 ──
        st.markdown("#### 買ったら記録する")
        st.caption("実際に発注して約定したら、ここで記録すると保有に反映されます。"
                   "約定価格が違った場合は数字を直してから押してください。")
        edited = st.data_editor(
            pd.DataFrame({
                "記録する": True,
                "コード": plan["コード"].values,
                "銘柄名": plan["銘柄名"].values,
                "株数": plan["株数"].values,
                "約定単価": plan["株価"].round(1).values,
            }),
            hide_index=True, width="stretch", key="buy_editor",
            column_config={
                "記録する": st.column_config.CheckboxColumn(),
                "コード": st.column_config.TextColumn(disabled=True),
                "銘柄名": st.column_config.TextColumn(disabled=True),
                "株数": st.column_config.NumberColumn(min_value=0, step=100),
                "約定単価": st.column_config.NumberColumn(format="¥%.1f", min_value=0.0),
            })
        buy_date = st.date_input("約定日", value=date.today(), key="buy_date")
        if st.button("この内容で買ったことにする", type="primary"):
            rows = edited[edited["記録する"] & (edited["株数"] > 0)]
            if rows.empty:
                st.warning("記録する行がありません")
            else:
                n = 0
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
                            new_shares = float(cur["shares"]) + shares
                            new_cost = ((float(cur["shares"]) * float(cur["avg_cost"] or 0)
                                         + shares * price) / new_shares)
                        else:
                            new_shares, new_cost = shares, price
                        conn.execute(
                            "INSERT OR REPLACE INTO holdings (account, ticker, name, shares, "
                            "avg_cost, target_yield, bottom_yield, updated_at) VALUES (?, ?, ?, ?, ?,"
                            " COALESCE((SELECT target_yield FROM holdings WHERE account=? AND ticker=?), 0.047),"
                            " (SELECT bottom_yield FROM holdings WHERE account=? AND ticker=?),"
                            " datetime('now'))",
                            (account, ticker, r.銘柄名, new_shares, new_cost,
                             account, ticker, account, ticker))
                        n += 1
                st.success(f"{n} 銘柄を記録しました（{'NISA' if account == 'nisa' else '特定口座'}）。"
                           "ポートフォリオに反映されています。")
                st.cache_data.clear()

    st.divider()
    st.subheader("業種の空き枠")
    exposure = sector_exposure(positions, config)
    if not exposure.empty:
        ex = exposure.rename(columns={"sector33": "業種"}).copy()
        ex["構成比"] = to_pct(ex["構成比"])
        st.dataframe(ex, hide_index=True, width="stretch", height=360, column_config={
            "評価額": st.column_config.NumberColumn(format="¥%d"),
            "年間配当": st.column_config.NumberColumn(format="¥%d"),
            "構成比": st.column_config.NumberColumn(format="%.1f%%"),
            "上限までの余裕": st.column_config.NumberColumn(format="¥%d"),
        })

# ═══════════════════════════════════════════════════ 整理する
with tab_sell:
    st.caption("売るかどうかはケンが決めます。ここは材料を並べて、決めたことを記録する場所です。")
    if review.empty:
        st.success("整理を検討すべき保有はありません")
    else:
        n_big = int((review["重さ"] == "重大").sum())
        n_small = int((review["重さ"] == "軽微").sum())
        c1, c2, c3 = st.columns([1, 1, 3])
        c1.metric("🔴 配当が危ない", n_big)
        c2.metric("🟡 額が小さいだけ", n_small)
        with c3:
            st.caption("**重大** ＝ 減配した・採用基準を外れた・配当継続スコアが低い。"
                       "配当そのものが危ない銘柄。\n\n"
                       "**軽微** ＝ 配当に問題はないが、保有額が小さく監視の手間に見合わない銘柄。\n\n"
                       "判断に使っているのは**配当継続スコア**です。発掘スコアは市場に"
                       "気づかれていないかを含み大型株が構造的に低く出るため、保有には使いません。")
        pick_sev = st.multiselect("表示する重さ", ["重大", "軽微"], default=["重大"])
        shown = review[review["重さ"].isin(pick_sev)] if pick_sev else review

        rev = shown[["重さ", "code", "name", "sector33", "account", "shares", "last_close",
                     "eval_value", "pnl_pct", "health", "整理を検討する理由"]].rename(columns={
            "code": "コード", "name": "銘柄名", "sector33": "業種", "account": "口座",
            "shares": "株数", "last_close": "株価", "eval_value": "評価額",
            "pnl_pct": "損益率", "health": "配当継続スコア"}).copy()
        rev["損益率"] = to_pct(rev["損益率"])
        rev["口座"] = rev["口座"].map({"specific": "特定", "nisa": "NISA"}).fillna(rev["口座"])
        st.dataframe(rev, hide_index=True, width="stretch", height=340, column_config={
            "株価": st.column_config.NumberColumn(format="¥%d"),
            "評価額": st.column_config.NumberColumn(format="¥%d"),
            "損益率": st.column_config.NumberColumn(format="%.1f%%"),
            "配当継続スコア": st.column_config.NumberColumn(format="%.0f"),
            "整理を検討する理由": st.column_config.TextColumn(width="large"),
        })

        st.markdown("#### 売ったら記録する")
        st.caption("実際に売却して約定したら、ここで記録すると保有から減ります。"
                   "一部だけ売った場合は株数を直してください。")
        sell_edit = st.data_editor(
            pd.DataFrame({
                "記録する": False,
                "コード": shown["code"].values,
                "銘柄名": shown["name"].values,
                "口座": shown["account"].map({"specific": "特定", "nisa": "NISA"}).values,
                "株数": shown["shares"].values,
                "約定単価": shown["last_close"].round(1).values,
            }),
            hide_index=True, width="stretch", key="sell_editor", height=280,
            column_config={
                "記録する": st.column_config.CheckboxColumn(),
                "コード": st.column_config.TextColumn(disabled=True),
                "銘柄名": st.column_config.TextColumn(disabled=True),
                "口座": st.column_config.TextColumn(disabled=True),
                "株数": st.column_config.NumberColumn(min_value=0, step=100),
                "約定単価": st.column_config.NumberColumn(format="¥%.1f", min_value=0.0),
            })
        sell_date = st.date_input("約定日", value=date.today(), key="sell_date")
        picked = sell_edit[sell_edit["記録する"] & (sell_edit["株数"] > 0)]
        if not picked.empty:
            st.info(f"**{len(picked)} 銘柄・{yen((picked['株数'] * picked['約定単価']).sum())}** "
                    "を売却として記録します")
        if st.button("この内容で売ったことにする", type="primary"):
            if picked.empty:
                st.warning("記録する行がありません")
            else:
                n, proceeds = 0, 0.0
                with connect() as conn:
                    for r in picked.itertuples(index=False):
                        ticker = f"{r.コード}.T"
                        acc = "nisa" if r.口座 == "NISA" else "specific"
                        shares, price = float(r.株数), float(r.約定単価)
                        conn.execute(
                            "INSERT INTO transactions (date, account, ticker, name, type, "
                            "shares, price, fee, memo) VALUES (?, ?, ?, ?, 'sell', ?, ?, 0, ?)",
                            (sell_date.isoformat(), acc, ticker, r.銘柄名, shares, price,
                             "整理タブから記録"))
                        cur = conn.execute(
                            "SELECT shares FROM holdings WHERE account=? AND ticker=?",
                            (acc, ticker)).fetchone()
                        held_n = float(cur["shares"]) if cur else 0.0
                        left = held_n - shares
                        if left <= 0.5:
                            # 全部売ったので保有から消す。取得単価は残さない。
                            conn.execute("DELETE FROM holdings WHERE account=? AND ticker=?",
                                         (acc, ticker))
                        else:
                            # 一部売却では取得単価は変えない（残った株の原価は同じ）
                            conn.execute("UPDATE holdings SET shares=?, updated_at=datetime('now') "
                                         "WHERE account=? AND ticker=?", (left, acc, ticker))
                        n += 1
                        proceeds += shares * price
                st.success(f"{n} 銘柄の売却を記録しました。売却代金 {yen(proceeds)}。\n\n"
                           "「買う」タブに戻ると、この資金を配分に回せます。")
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
                    "金額": st.column_config.NumberColumn(format="¥%d"),
                })
