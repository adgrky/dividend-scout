"""ポートフォリオ — 発掘した銘柄を入れる枠の現状。"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from datetime import date

from modules.format import pct, to_pct, yen, yen_short
from modules.portfolio import (dividend_calendar, dividends_received,
                               expected_dividends, record_equity, sector_exposure)
from modules.store import connect, read_df
from modules.ui import get_config, get_positions

st.title("📊 ポートフォリオ")

config = get_config()
positions = get_positions(config)
if positions.empty:
    st.warning("保有データがありません。\n\n```bash\nuv run python scripts/import_legacy.py\n```")
    st.stop()

total_eval = positions["eval_value"].sum()
total_cost = positions["cost_value"].sum()
annual_div = positions["annual_dividend"].sum()
annual_div_at = positions["annual_dividend_after_tax"].sum()

# 指標は3列ずつ2段に分ける。5列に詰めると値が「1,27…」と切れて読めなくなる。
c1, c2, c3 = st.columns(3)
c1.metric("評価額", yen_short(total_eval))
c2.metric("損益", yen_short(total_eval - total_cost),
          f"{(total_eval / total_cost - 1):.1%}" if total_cost else None)
c3.metric("取得額", yen_short(total_cost))

c4, c5, c6 = st.columns(3)
c4.metric("年間配当（税引前）", yen_short(annual_div))
c5.metric("年間配当（税引後）", yen_short(annual_div_at),
          help="NISAは非課税、特定口座は20.315%を引いています")
c6.metric("YOC（取得額に対する利回り）", pct(annual_div / total_cost) if total_cost else "—",
          help="いまの年間配当 ÷ 買ったときの金額。増配で育つとここが上がります")

n_ok = int((positions["gate_passed"] == 1).sum())
st.caption(f"保有 {len(positions)} 銘柄 ／ 1銘柄あたり平均 {yen_short(total_eval / len(positions))}"
           f" ／ 採用基準を満たすもの {n_ok} 銘柄")

# その日の評価額と年間配当を1日1行だけ残す。
# インカム投資の目的は「配当が育つこと」なので、推移が見えないと成果が分からない。
record_equity(positions, config)

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["保有一覧", "業種の配分", "配当月", "配当の受取記録", "推移"])

with tab1:
    edit_mode = st.toggle("株数と取得単価を直す", value=False,
                          help="売買を証券会社の画面で済ませたあと、ここで合わせられます。"
                               "株数を0にすると保有から消えます")
    src = positions.copy()
    src["判定"] = src.apply(
        lambda r: "◯ 基準を満たす" if r.get("gate_passed") == 1
        else (str(r.get("gate_reason")) if isinstance(r.get("gate_reason"), str)
              and r.get("gate_reason") else "—"), axis=1)

    if edit_mode:
        st.caption("**直した行は下のボタンを押すまで保存されません。** "
                   "株数を0にすると、その保有は消えます。"
                   "売買として記録を残したいときは 💰 資金投入 の「買う」「整理する」を使ってください"
                   "（そちらは取引の履歴も残ります）。")
        base = src[["account", "ticker", "code", "name", "shares", "avg_cost",
                    "last_close", "target_yield"]].copy()
        base["口座"] = base["account"].map({"specific": "特定", "nisa": "NISA"})
        ed = st.data_editor(
            base.rename(columns={"code": "コード", "name": "銘柄名", "shares": "株数",
                                 "avg_cost": "取得単価", "last_close": "株価",
                                 "target_yield": "目標利回り"})[
                ["コード", "銘柄名", "口座", "株数", "取得単価", "株価", "目標利回り"]],
            hide_index=True, width="stretch", height=560, key="holdings_editor",
            column_config={
                "コード": st.column_config.TextColumn(disabled=True),
                "銘柄名": st.column_config.TextColumn(disabled=True),
                "口座": st.column_config.TextColumn(disabled=True),
                "株価": st.column_config.NumberColumn(format="¥%d", disabled=True),
                "株数": st.column_config.NumberColumn(min_value=0.0, step=100.0),
                "取得単価": st.column_config.NumberColumn(format="¥%.1f", min_value=0.0),
                "目標利回り": st.column_config.NumberColumn(
                    format="%.3f", min_value=0.0, max_value=0.2,
                    help="0.047 なら 4.7%。指値の計算に使います"),
            })
        if st.button("この内容で保存する", type="primary"):
            n_upd = n_del = 0
            with connect() as conn:
                for orig, new_row in zip(base.itertuples(index=False),
                                         ed.itertuples(index=False)):
                    sh, cost, tgt = float(new_row.株数), float(new_row.取得単価), float(new_row.目標利回り)
                    if (abs(sh - float(orig.shares)) < 1e-9
                            and abs(cost - float(orig.avg_cost or 0)) < 1e-9
                            and abs(tgt - float(orig.target_yield or 0)) < 1e-9):
                        continue
                    if sh <= 0:
                        conn.execute("DELETE FROM holdings WHERE account=? AND ticker=?",
                                     (orig.account, orig.ticker))
                        n_del += 1
                    else:
                        conn.execute(
                            "UPDATE holdings SET shares=?, avg_cost=?, target_yield=?, "
                            "updated_at=datetime('now') WHERE account=? AND ticker=?",
                            (sh, cost, tgt, orig.account, orig.ticker))
                        n_upd += 1
            if n_upd or n_del:
                st.success(f"{n_upd} 銘柄を直し、{n_del} 銘柄を消しました")
                st.cache_data.clear()
                st.rerun()
            else:
                st.info("変わったところがありません")
    else:
        view = src[[
            "code", "name", "sector33", "account", "shares", "avg_cost", "last_close",
            "eval_value", "pnl_pct", "dps_latest", "annual_dividend", "current_yield",
            "yoc", "streak", "health", "判定",
        ]].rename(columns={
            "code": "コード", "name": "銘柄名", "sector33": "業種", "account": "口座",
            "shares": "株数", "avg_cost": "取得単価", "last_close": "株価",
            "eval_value": "評価額", "pnl_pct": "損益率", "dps_latest": "DPS",
            "annual_dividend": "年間配当", "current_yield": "現在利回り",
            "yoc": "YOC", "streak": "連続増配", "health": "配当継続スコア",
        }).sort_values("評価額", ascending=False)
        for col in ("損益率", "現在利回り", "YOC"):
            view[col] = to_pct(view[col])
        view["口座"] = view["口座"].map({"specific": "特定", "nisa": "NISA"}).fillna(view["口座"])

        st.dataframe(view, hide_index=True, width="stretch", height=560, column_config={
            "取得単価": st.column_config.NumberColumn(format="¥%.1f"),
            "株価": st.column_config.NumberColumn(format="¥%d"),
            "評価額": st.column_config.NumberColumn(format="¥%d"),
            "年間配当": st.column_config.NumberColumn(format="¥%d"),
            "DPS": st.column_config.NumberColumn(format="¥%.1f"),
            "損益率": st.column_config.NumberColumn(format="%.1f%%"),
            "現在利回り": st.column_config.NumberColumn(format="%.2f%%"),
            "YOC": st.column_config.NumberColumn(
                format="%.2f%%", help="いまの年間配当 ÷ 買ったときの金額"),
            "連続増配": st.column_config.NumberColumn(format="%d 年"),
            "配当継続スコア": st.column_config.NumberColumn(
                format="%.0f", help="配当が続くか・増えるかだけを見た点数。"
                                    "空欄は採用基準を外れている銘柄（理由は「判定」列）"),
            "判定": st.column_config.TextColumn(width="large"),
        })
        st.download_button("保有一覧をCSVで保存",
                           view.to_csv(index=False).encode("utf-8-sig"),
                           f"保有一覧_{date.today():%Y%m%d}.csv", "text/csv")

with tab2:
    exposure = sector_exposure(positions, config)
    over = exposure[exposure["上限超過"]]
    if not over.empty:
        st.warning("上限（構成比20%）を超えている業種：" + "、".join(
            f"{r['sector33']}（{r['構成比']:.1%}）" for _, r in over.iterrows()))
    ex = exposure.rename(columns={"sector33": "業種"}).copy()
    fig = go.Figure(go.Bar(
        x=to_pct(ex["構成比"]), y=ex["業種"], orientation="h",
        marker_color=["#E45756" if o else "#4C8BF5" for o in ex["上限超過"]]))
    fig.add_vline(x=config["portfolio"]["max_sector_weight"] * 100,
                  line_dash="dash", annotation_text="上限")
    fig.update_layout(height=max(400, 22 * len(ex)), xaxis_title="構成比（%）",
                      margin=dict(l=10, r=10, t=10, b=10),
                      yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch")
    ex["構成比"] = to_pct(ex["構成比"])
    st.dataframe(ex, hide_index=True, width="stretch", column_config={
        "評価額": st.column_config.NumberColumn(format="¥%d"),
        "年間配当": st.column_config.NumberColumn(format="¥%d"),
        "構成比": st.column_config.NumberColumn(format="%.1f%%"),
        "上限までの余裕": st.column_config.NumberColumn(format="¥%d"),
    })

with tab3:
    cal = dividend_calendar(positions)
    if cal.empty:
        st.caption("配当履歴が不足しています")
    else:
        fig = go.Figure(go.Bar(x=cal["label"], y=cal["amount"], marker_color="#4C8BF5"))
        fig.update_layout(height=340, yaxis_title="受取見込み（円）",
                          margin=dict(l=10, r=10, t=10, b=10),
                          xaxis=dict(categoryorder="array",
                                     categoryarray=[f"{m}月" for m in range(1, 13)]))
        st.plotly_chart(fig, width="stretch")
        peak = cal.loc[cal["amount"].idxmax()]
        share = peak["amount"] / cal["amount"].sum() if cal["amount"].sum() else 0
        empty_months = [r["label"] for _, r in cal.iterrows() if r["amount"] == 0]
        st.caption(f"直近1年の権利落ち月ベース。最も多いのは **{peak['label']}**"
                   f"（{yen(peak['amount'])}・全体の {share:.0%}）。")
        if empty_months:
            st.info(f"**受け取りがゼロの月：{'、'.join(empty_months)}**　\n"
                    "🔭 発掘 の「配当がある月」でこれらの月を選ぶと、"
                    "受取を平準化できる銘柄を探せます。")

with tab4:
    st.caption("実際に受け取った配当を記録します。**税引後の手取り額**を入れてください。"
               "予想ではなく実績が貯まると、インカムが本当に育っているかが分かります。")
    got = dividends_received(config)

    if not got.empty:
        this_year = got[got["年"] == date.today().year]
        c1, c2, c3 = st.columns(3)
        c1.metric(f"{date.today().year}年の受取（税引後）", yen(this_year["受取額"].sum()))
        c2.metric("累計の受取", yen(got["受取額"].sum()))
        c3.metric("記録件数", f"{len(got)} 件")

        by_year = got.groupby("年")["受取額"].sum().reset_index()
        fig = go.Figure(go.Bar(x=by_year["年"].astype(str), y=by_year["受取額"],
                               marker_color="#4C8BF5",
                               text=[f"{v:,.0f}円" for v in by_year["受取額"]],
                               textposition="outside"))
        fig.update_layout(height=300, yaxis_title="受け取った配当（円・税引後）",
                          margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("まだ記録がありません。証券会社の配当金計算書を見ながら、下で足していけます。")

    st.markdown("#### 保有と配当履歴から自動で作る")
    st.caption("**証券会社の計算書を1件ずつ写す必要はありません。** "
               "権利落ち日と、いまの株数から、受け取ったはずの配当を組み立てます。"
               "金額はその場で直せます。")
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        months = st.selectbox("さかのぼる期間", [6, 12, 24, 36], index=1,
                              format_func=lambda m: f"過去 {m} か月")
    with c2:
        lag = st.number_input("権利落ちから入金までの日数", 30, 150, 75, 5,
                              help="日本株はおおむね2〜3か月後に入金されます")
    with c3:
        st.caption("⚠️ 株数は**いまの保有数**で計算しています。"
                   "その時点で株数が違っていた銘柄は、下の表で直してください。"
                   "すでに記録した月は出てきません。")

    exp = expected_dividends(positions, config, months_back=int(months), lag_days=int(lag))
    if exp.empty:
        st.success("自動で作れる未記録の配当はありません（すべて記録済みか、履歴がありません）")
    else:
        st.info(f"**{len(exp)} 件・合計 {yen(exp['受取額（税引後）'].sum())}（税引後）** "
                "が未記録です。内容を確かめて、下のボタンでまとめて記録できます。")
        ed = st.data_editor(
            exp.drop(columns=["_ticker", "_account"]),
            hide_index=True, width="stretch", height=380, key="auto_div_editor",
            column_config={
                "記録する": st.column_config.CheckboxColumn(),
                "コード": st.column_config.TextColumn(disabled=True),
                "銘柄名": st.column_config.TextColumn(disabled=True),
                "口座": st.column_config.TextColumn(disabled=True),
                "権利落ち日": st.column_config.DateColumn(disabled=True),
                "受取日": st.column_config.DateColumn(),
                "株数": st.column_config.NumberColumn(min_value=0.0, step=100.0),
                "1株配当": st.column_config.NumberColumn(format="¥%.2f", disabled=True),
                "税引前": st.column_config.NumberColumn(format="¥%d", disabled=True),
                "受取額（税引後）": st.column_config.NumberColumn(format="¥%d", min_value=0.0),
            })
        picked = ed[ed["記録する"] & (ed["受取額（税引後）"] > 0)]
        st.caption(f"チェックが入っているのは **{len(picked)} 件・"
                   f"{yen(picked['受取額（税引後）'].sum())}** です。")
        if st.button(f"この {len(picked)} 件を記録する", type="primary",
                     disabled=picked.empty):
            with connect() as conn:
                for i, r in picked.iterrows():
                    src = exp.loc[i]
                    conn.execute(
                        "INSERT INTO transactions (date, account, ticker, name, type, "
                        "shares, price, fee, memo) VALUES (?, ?, ?, ?, 'dividend', 1, ?, 0, ?)",
                        (str(r["受取日"]), src["_account"], src["_ticker"], src["銘柄名"],
                         float(r["受取額（税引後）"]),
                         f"自動生成（権利落ち {src['権利落ち日']}／{r['株数']:.0f}株）"))
            st.success(f"{len(picked)} 件・{yen(picked['受取額（税引後）'].sum())} を記録しました")
            st.cache_data.clear()
            st.rerun()

    with st.expander("1件だけ手で足す"):
        holds = positions[["ticker", "name", "account", "shares"]].drop_duplicates()
        labels = holds.apply(
            lambda r: f"{r['ticker'][:-2]} {r['name']}（{'NISA' if r['account'] == 'nisa' else '特定'}）",
            axis=1).tolist()
        lookup = dict(zip(labels, holds.itertuples(index=False)))
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            pick = st.selectbox("銘柄", [""] + labels)
        with c2:
            recv_date = st.date_input("受取日", value=date.today())
        with c3:
            amount = st.number_input("受取額（税引後・円）", 0, 10_000_000, 0, 100)
        with c4:
            st.write("")
            st.write("")
            add = st.button("記録する")
        if add:
            if not pick or amount <= 0:
                st.warning("銘柄と受取額を入れてください")
            else:
                row = lookup[pick]
                with connect() as conn:
                    conn.execute(
                        "INSERT INTO transactions (date, account, ticker, name, type, shares, "
                        "price, fee, memo) VALUES (?, ?, ?, ?, 'dividend', 1, ?, 0, ?)",
                        (recv_date.isoformat(), row.account, row.ticker, row.name,
                         float(amount), "手入力（税引後）"))
                st.success(f"{row.name} の配当 {yen(amount)} を記録しました")
                st.cache_data.clear()
                st.rerun()

    if not got.empty:
        st.markdown("#### 記録の一覧")
        view = got[["date", "account", "ticker", "name", "受取額"]].copy()
        view["date"] = view["date"].dt.strftime("%Y-%m-%d")
        view["account"] = view["account"].map({"specific": "特定", "nisa": "NISA"}).fillna(view["account"])
        st.dataframe(view.rename(columns={
            "date": "受取日", "account": "口座", "ticker": "銘柄", "name": "銘柄名"}).iloc[::-1],
            hide_index=True, width="stretch", height=300,
            column_config={"受取額": st.column_config.NumberColumn(format="¥%d")})

with tab5:
    hist = read_df("SELECT * FROM equity_history ORDER BY date")
    st.caption("アプリを開くたびに、その日の評価額と年間配当を1行だけ残しています。"
               "**インカム投資で見るべきは年間配当のほう**です。"
               "評価額は市場が決めますが、年間配当は増配と買い増しで自分が育てられます。")

    if hist.empty:
        st.info("まだ記録がありません。次にこの画面を開いたときから貯まります。")
    else:
        first, last = hist.iloc[0], hist.iloc[-1]
        days = len(hist)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("記録した日数", f"{days} 日",
                  help=f"{first['date']} から {last['date']} まで")
        d_div = last["annual_dividend"] - first["annual_dividend"]
        c2.metric("年間配当（税引前）", yen(last["annual_dividend"]),
                  yen(d_div) if days > 1 else None,
                  help="いまの保有株数 × 直近の1株配当。増配と買い増しで増えます")
        c3.metric("年間配当（税引後）", yen(last["annual_dividend_after_tax"]),
                  yen(last["annual_dividend_after_tax"] - first["annual_dividend_after_tax"])
                  if days > 1 else None)
        c4.metric("YOC", pct(last["yoc"], 2) if pd.notna(last["yoc"]) else "—",
                  f"{(last['yoc'] - first['yoc']) * 100:+.2f}pt" if days > 1
                  and pd.notna(first["yoc"]) else None,
                  help="いまの年間配当 ÷ 買ったときの金額。増配で育つとここが上がります")

        if days < 2:
            st.info("グラフは2日ぶん貯まってから出ます。いまは1日ぶんしかありません。")
        else:
            c1, c2 = st.columns(2)
            with c1:
                fig = go.Figure()
                fig.add_scatter(x=hist["date"], y=hist["total_eval"], name="評価額",
                                line=dict(color="#4C8BF5"))
                fig.add_scatter(x=hist["date"], y=hist["total_cost"], name="取得額",
                                line=dict(color="#999", dash="dot"))
                fig.update_layout(height=300, yaxis_title="円", title="評価額と取得額",
                                  margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig, width="stretch")
            with c2:
                fig = go.Figure()
                fig.add_scatter(x=hist["date"], y=hist["annual_dividend"],
                                name="税引前", line=dict(color="#E45756"))
                fig.add_scatter(x=hist["date"], y=hist["annual_dividend_after_tax"],
                                name="税引後", line=dict(color="#E45756", dash="dot"))
                fig.update_layout(height=300, yaxis_title="円",
                                  title="年間配当（こちらが本番）",
                                  margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig, width="stretch")

        # 生の列名のまま出すと何の数字か分からないので、必ず日本語に直して単位をつける
        show = pd.DataFrame({
            "日付": hist["date"],
            "評価額": hist["total_eval"].round(0),
            "取得額": hist["total_cost"].round(0),
            "損益": (hist["total_eval"] - hist["total_cost"]).round(0),
            "年間配当（税引前）": hist["annual_dividend"].round(0),
            "年間配当（税引後）": hist["annual_dividend_after_tax"].round(0),
            "保有銘柄数": hist["holdings_count"],
            "YOC": to_pct(hist["yoc"]).round(2),
        }).iloc[::-1]
        st.dataframe(show, hide_index=True, width="stretch", height=280, column_config={
            "評価額": st.column_config.NumberColumn(format="¥%d"),
            "取得額": st.column_config.NumberColumn(format="¥%d"),
            "損益": st.column_config.NumberColumn(format="¥%d"),
            "年間配当（税引前）": st.column_config.NumberColumn(format="¥%d"),
            "年間配当（税引後）": st.column_config.NumberColumn(format="¥%d"),
            "保有銘柄数": st.column_config.NumberColumn(format="%d 銘柄"),
            "YOC": st.column_config.NumberColumn(
                format="%.2f%%", help="いまの年間配当 ÷ 買ったときの金額"),
        })
