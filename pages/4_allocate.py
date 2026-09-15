"""資金投入 — 「トレード利益を移した。どこに入れるか」に答える。

配分案を出すだけでなく、**買ったら記録できる**ところまでを1画面で完結させる。
記録できないと、翌月には保有と画面がずれて使えなくなる。
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from modules.allocator import allocate, rebalance_funds
from modules.format import to_pct, yen, yen_short
from modules.portfolio import review_candidates, sector_exposure
from modules.store import connect, read_df
from modules.ui import get_config, get_positions, get_scores, no_data_guard

st.title("💰 資金投入")
st.caption("入金額を、スコアと業種の空き枠に合わせて割り振る")

config = get_config()
scores = get_scores()
if no_data_guard(scores):
    st.stop()

positions = get_positions(config)
review = review_candidates(positions, config)

c1, c2, c3, c4 = st.columns(4)
with c1:
    cash = st.number_input("入金額（円）", 0, 100_000_000, 500_000, 50_000, format="%d")
    account = st.radio("入れる口座", ["specific", "nisa"], index=0,
                       format_func=lambda a: "特定口座（課税 20.315%）" if a == "specific"
                       else "NISA（非課税）",
                       help="NISAは配当が非課税です。同じ配当でも手取りが約2割変わります")
with c2:
    max_names = st.number_input("買う銘柄数の上限", 1, 20, 5,
                                help="分散させすぎると監視が回らなくなります")
    min_yield = st.number_input("最低利回り（%）", 0.0, 8.0, 3.0, 0.25,
                                help="これを下回る銘柄には資金を入れません") / 100
with c3:
    per_cap = st.slider("1銘柄あたりの上限（入金額に対する比率）", 10, 100, 30, 5,
                        format="%d%%") / 100
    scope = st.radio("対象", ["未保有のみ", "保有の買い増しも含む"], index=1)
with c4:
    require_target = st.checkbox("目標利回りに届いている銘柄だけに絞る", value=False,
                                 help="設定した目標利回りを下回る株価まで待つ、という考え方")
    sell_funds = rebalance_funds(review)
    use_sell = False
    if sell_funds > 0:
        use_sell = st.checkbox(
            f"整理候補 {len(review)} 銘柄（{yen_short(sell_funds)}）を売却した資金も充てる",
            value=False)

if use_sell:
    cash += sell_funds

held = set(positions["ticker"]) if not positions.empty else set()
cand = scores[scores["gate_passed"] == 1].copy().reset_index(drop=True)
raw = pd.DataFrame([json.loads(x)["raw"] for x in cand["detail_json"]])
cand = pd.concat([cand, raw[["dividend_yield", "yield_percentile", "streak"]]], axis=1)

if scope == "未保有のみ":
    cand = cand[~cand["ticker"].isin(held)]
cand = cand[cand["dividend_yield"].fillna(0) >= min_yield]

targets = read_df("SELECT ticker, target_yield FROM watchlist "
                  "UNION SELECT ticker, target_yield FROM holdings").groupby("ticker").first()
cand = cand.join(targets, on="ticker")

plan = allocate(cash, cand, positions, config, max_names=int(max_names),
                per_name_cap_pct=float(per_cap), require_below_target=require_target)

st.divider()
if plan.empty:
    st.warning("条件に合う配分先がありません。銘柄数の上限を増やすか、利回りの下限を下げてください。")
else:
    tax = config["portfolio"]["tax_rate_nisa"] if account == "nisa" \
        else config["portfolio"]["tax_rate_specific"]
    total_in = plan["投入額"].sum()
    total_div = plan["年間配当"].sum()
    after_tax = total_div * (1 - tax)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("投入額", yen(total_in))
    c2.metric("増える年間配当（税引前）", yen(total_div))
    c3.metric("増える年間配当（税引後）", yen(after_tax),
              help="NISAなら非課税、特定口座なら20.315%が引かれます")
    c4.metric("残り", yen(cash - total_in))

    show = plan[["コード", "銘柄名", "業種", "株価", "株数", "投入額", "利回り",
                 "年間配当", "スコア", "自己利回り順位", "連続増配", "業種の空き枠"]].copy()
    show["利回り"] = to_pct(show["利回り"])
    show["自己利回り順位"] = to_pct(show["自己利回り順位"])
    show["税引後配当"] = (show["年間配当"] * (1 - tax)).round(0)
    show["業種の空き枠"] = (show["業種の空き枠"] / 1e4).round(0)
    show = show[["コード", "銘柄名", "業種", "株価", "株数", "投入額", "利回り",
                 "年間配当", "税引後配当", "スコア", "自己利回り順位", "連続増配", "業種の空き枠"]]

    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "株価": st.column_config.NumberColumn(format="¥%d"),
        "投入額": st.column_config.NumberColumn(format="¥%d"),
        "年間配当": st.column_config.NumberColumn(format="¥%d"),
        "税引後配当": st.column_config.NumberColumn(format="¥%d"),
        "利回り": st.column_config.NumberColumn(format="%.2f%%"),
        "スコア": st.column_config.NumberColumn(format="%.0f"),
        "自己利回り順位": st.column_config.ProgressColumn(
            format="%.0f%%", min_value=0, max_value=100),
        "連続増配": st.column_config.NumberColumn(format="%d 年"),
        "業種の空き枠": st.column_config.NumberColumn(
            format="%d 万円", help="この業種にあと何円まで入れられるか（構成比20%が上限）"),
    })
    st.caption("株数は単元（100株）に丸めています。"
               "スコアが高い順に、業種の上限と1銘柄あたりの上限を守りながら埋めています。")

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

    trade_date = st.date_input("約定日", value=date.today())
    if st.button("この内容で買ったことにする", type="primary"):
        rows = edited[edited["記録する"] & (edited["株数"] > 0)]
        if rows.empty:
            st.warning("記録する行がありません")
        else:
            n = 0
            with connect() as conn:
                for r in rows.itertuples(index=False):
                    ticker = f"{r.コード}.T"
                    shares = float(r.株数)
                    price = float(r.約定単価)
                    conn.execute(
                        "INSERT INTO transactions (date, account, ticker, name, type, "
                        "shares, price, fee, memo) VALUES (?, ?, ?, ?, 'buy', ?, ?, 0, ?)",
                        (trade_date.isoformat(), account, ticker, r.銘柄名, shares, price,
                         "資金投入画面から記録"))
                    # 既存保有があれば株数を足して取得単価を加重平均で更新する
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
                        "avg_cost, target_yield, bottom_yield, updated_at) VALUES "
                        "(?, ?, ?, ?, ?, "
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
st.subheader("整理を検討する保有")
if review.empty:
    st.success("基準を外れた保有はありません")
else:
    n_big = int((review["重さ"] == "重大").sum())
    n_small = int((review["重さ"] == "軽微").sum())
    c1, c2 = st.columns([1, 3])
    c1.metric("🔴 配当が危ない", n_big)
    c1.metric("🟡 額が小さいだけ", n_small)
    with c2:
        st.caption("**重大** ＝ 減配した・採用基準を外れた・配当継続スコアが低い。配当そのものが危ない銘柄。\n\n"
                   "**軽微** ＝ 配当に問題はないが、保有額が小さく監視の手間に見合わない銘柄。\n\n"
                   "判断に使っているのは**配当継続スコア**（配当が続くかだけを見た点数）です。"
                   "発掘スコアは市場に気づかれていないかを含むので大型株が構造的に低く出るため、保有の判断には使いません。")
        only_big = st.checkbox("配当が危ないものだけ表示", value=True)
    if only_big:
        review = review[review["重さ"] == "重大"]
    rev = review[["重さ", "code", "name", "sector33", "account", "shares", "eval_value",
                  "pnl_pct", "health", "整理を検討する理由"]].rename(columns={
        "code": "コード", "name": "銘柄名", "sector33": "業種", "account": "口座",
        "shares": "株数", "eval_value": "評価額", "pnl_pct": "損益率",
        "health": "配当継続スコア"}).copy()
    rev["損益率"] = to_pct(rev["損益率"])
    rev["口座"] = rev["口座"].map({"specific": "特定", "nisa": "NISA"}).fillna(rev["口座"])
    st.dataframe(rev, hide_index=True, width="stretch", height=380, column_config={
        "評価額": st.column_config.NumberColumn(format="¥%d"),
        "損益率": st.column_config.NumberColumn(format="%.1f%%"),
        "配当継続スコア": st.column_config.NumberColumn(format="%.0f"),
        "整理を検討する理由": st.column_config.TextColumn(width="large"),
    })

st.divider()
st.subheader("業種の空き枠")
exposure = sector_exposure(positions, config)
if not exposure.empty:
    exposure = exposure.rename(columns={"sector33": "業種"}).copy()
    exposure["構成比"] = to_pct(exposure["構成比"])
    st.dataframe(exposure, hide_index=True, width="stretch", height=400, column_config={
        "評価額": st.column_config.NumberColumn(format="¥%d"),
        "年間配当": st.column_config.NumberColumn(format="¥%d"),
        "構成比": st.column_config.NumberColumn(format="%.1f%%"),
        "上限までの余裕": st.column_config.NumberColumn(format="¥%d"),
    })
