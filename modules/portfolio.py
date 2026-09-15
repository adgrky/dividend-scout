"""保有ポートフォリオの評価。

発掘した銘柄を「どの枠に入れるか」を判断するための材料を作る。
単体で見ても意味が薄く、業種の偏りと配当月の偏りが分かって初めて役に立つ。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.store import read_df


def load_positions(config: dict) -> pd.DataFrame:
    """保有に株価・配当・業種を結合して、評価額と配当を計算する。"""
    h = read_df("SELECT * FROM holdings")
    if h.empty:
        return h
    q = read_df("SELECT ticker, last_close, pos_52w FROM quotes").set_index("ticker")
    u = read_df("SELECT ticker, code, name AS name_jpx, sector33, market FROM universe").set_index("ticker")
    sc = read_df(
        "SELECT ticker, total, health, capacity, willingness, growth, neglect, valuation, "
        "trap_penalty, gate_passed, gate_reason, detail_json FROM scores "
        "WHERE asof = (SELECT MAX(asof) FROM scores)"
    ).set_index("ticker")

    # 保有銘柄ぶんだけ作る。全銘柄（3,707）ぶん作ると画面が20秒以上固まる。
    tickers = sorted(set(h["ticker"]))
    ph = ",".join("?" * len(tickers))
    div = read_df(f"SELECT ticker, date, amount FROM dividends WHERE ticker IN ({ph})",
                  tuple(tickers))
    from modules.dividend_history import build_profiles, profiles_to_frame
    prof = profiles_to_frame(build_profiles(div))

    df = h.set_index("ticker").join([q, u, sc, prof], how="left").reset_index()
    df["name"] = df["name"].fillna(df["name_jpx"])
    df["eval_value"] = df["shares"] * df["last_close"]
    df["cost_value"] = df["shares"] * df["avg_cost"]
    df["pnl"] = df["eval_value"] - df["cost_value"]
    df["pnl_pct"] = np.where(df["cost_value"] > 0, df["pnl"] / df["cost_value"], np.nan)
    df["annual_dividend"] = df["shares"] * df["dps_latest"].fillna(0)

    rate_s = config["portfolio"]["tax_rate_specific"]
    rate_n = config["portfolio"]["tax_rate_nisa"]
    df["annual_dividend_after_tax"] = df["annual_dividend"] * np.where(
        df["account"] == "nisa", 1 - rate_n, 1 - rate_s)

    # YOC（取得価格に対する利回り）— 育った配当の見え方はここに出る
    df["yoc"] = np.where(df["cost_value"] > 0, df["annual_dividend"] / df["cost_value"], np.nan)
    df["current_yield"] = np.where(df["eval_value"] > 0,
                                   df["annual_dividend"] / df["eval_value"], np.nan)
    return df


def sector_exposure(pos: pd.DataFrame, config: dict) -> pd.DataFrame:
    """33業種ベースの配分。上限超過を明示する。"""
    if pos.empty:
        return pd.DataFrame()
    total = pos["eval_value"].sum()
    g = pos.groupby("sector33", dropna=False).agg(
        銘柄数=("ticker", "count"),
        評価額=("eval_value", "sum"),
        年間配当=("annual_dividend", "sum"),
    ).sort_values("評価額", ascending=False)
    g["構成比"] = g["評価額"] / total if total else np.nan
    cap = config["portfolio"]["max_sector_weight"]
    g["上限超過"] = g["構成比"] > cap
    g["上限までの余裕"] = (cap * total - g["評価額"]).clip(lower=0)
    return g.reset_index()


def dividend_calendar(pos: pd.DataFrame) -> pd.DataFrame:
    """月別の配当受取見込み。特定の月に偏っているかを見る。"""
    if pos.empty:
        return pd.DataFrame()
    tickers = sorted(set(pos["ticker"]))
    ph = ",".join("?" * len(tickers))
    div = read_df(f"SELECT ticker, date, amount FROM dividends WHERE ticker IN ({ph})",
                  tuple(tickers))
    if div.empty:
        return pd.DataFrame()
    div["date"] = pd.to_datetime(div["date"])
    recent = div[div["date"] >= div["date"].max() - pd.DateOffset(years=2)]

    rows = []
    shares = pos.set_index("ticker")["shares"].groupby(level=0).sum()
    for ticker, g in recent.groupby("ticker"):
        if ticker not in shares.index:
            continue
        # 直近1年ぶんの権利落ち月と金額
        last12 = g[g["date"] > g["date"].max() - pd.DateOffset(years=1)]
        for _, r in last12.iterrows():
            rows.append({"ticker": ticker, "month": int(r["date"].month),
                         "amount": r["amount"] * shares[ticker]})
    if not rows:
        return pd.DataFrame()
    cal = pd.DataFrame(rows).groupby("month", as_index=False)["amount"].sum()
    full = pd.DataFrame({"month": range(1, 13)}).merge(cal, on="month", how="left").fillna(0)
    full["label"] = full["month"].map(lambda m: f"{m}月")
    return full
def record_equity(pos: pd.DataFrame, config: dict) -> None:
    """その日の評価額と年間配当を残す。

    インカム投資の目的は「配当が育つこと」なので、評価額よりも
    **年間配当がいくらになったか**の推移が本番。1日1行だけ残す。
    """
    from datetime import date as _date
    from modules.store import upsert_df
    if pos is None or pos.empty:
        return
    cost = float(pos["cost_value"].sum())
    div = float(pos["annual_dividend"].sum())
    row = {
        "date": _date.today().isoformat(),
        "total_eval": float(pos["eval_value"].sum()),
        "total_cost": cost,
        "annual_dividend": div,
        "annual_dividend_after_tax": float(pos["annual_dividend_after_tax"].sum()),
        "holdings_count": int(len(pos)),
        "yoc": (div / cost) if cost else None,
    }
    upsert_df("equity_history", pd.DataFrame([row]),
              ["date", "total_eval", "total_cost", "annual_dividend",
               "annual_dividend_after_tax", "holdings_count", "yoc"])


def dividends_received(config: dict) -> pd.DataFrame:
    """実際に受け取った配当の記録（税引後）。"""
    from modules.store import read_df
    tx = read_df("SELECT date, account, ticker, name, shares, price, memo "
                 "FROM transactions WHERE type='dividend' ORDER BY date")
    if tx.empty:
        return tx
    tx["date"] = pd.to_datetime(tx["date"])
    # price に「1株あたりの受取額（税引後）」を入れる運用にする
    tx["受取額"] = tx["shares"] * tx["price"]
    tx["年"] = tx["date"].dt.year
    tx["月"] = tx["date"].dt.month
    return tx


def expected_dividends(pos: pd.DataFrame, config: dict, months_back: int = 12,
                       lag_days: int = 75) -> pd.DataFrame:
    """保有株数と配当履歴から、受け取ったはずの配当を組み立てる。

    証券会社の計算書を1件ずつ写すのは続かない。**権利落ち日 × 保有株数**から
    自動で作り、ケンは金額を直すだけにする。

    注意：株数はいまの保有数を使う。権利確定の時点で株数が違っていた場合は
    金額がずれるので、画面側で必ず直せるようにしておくこと。
    入金は権利落ちから2〜3か月後なので、受取日は lag_days 後を置く。
    """
    if pos is None or pos.empty:
        return pd.DataFrame()
    tickers = sorted(set(pos["ticker"]))
    ph = ",".join("?" * len(tickers))
    div = read_df(f"SELECT ticker, date, amount FROM dividends WHERE ticker IN ({ph})",
                  tuple(tickers))
    if div.empty:
        return pd.DataFrame()
    div["date"] = pd.to_datetime(div["date"])
    today = pd.Timestamp.today().normalize()
    since = today - pd.DateOffset(months=months_back)
    div = div[(div["date"] >= since) & (div["date"] <= today)]
    if div.empty:
        return pd.DataFrame()

    # すでに記録した配当は出さない。
    #
    # 鍵は **権利落ち日**。入金日で判定すると、「権利落ちから入金までの日数」を変えた
    # とたんに同じ配当がもう一度未記録として出てきて、二重に計上できてしまう
    # （実測: 75日→45日にしたら、記録済み224件が全部また未記録として出てきた）。
    #
    # ref_date を持たない古い記録は、入金日から「lag日前」を引くだけでは同じ問題が
    # 残る（引く日数が設定で動くため）。**その銘柄の実際の権利落ち日のうち、入金日
    # より前でいちばん近いもの**に吸着させる。設定を変えても答えが動かない。
    done = read_df("SELECT ticker, account, date, ref_date FROM transactions "
                   "WHERE type='dividend'")
    seen = set()
    if not done.empty:
        all_div = read_df("SELECT ticker, date FROM dividends")
        all_div["date"] = pd.to_datetime(all_div["date"])
        ex_by_ticker = {t: np.sort(g["date"].values)
                        for t, g in all_div.groupby("ticker")}
        for rec in done.itertuples(index=False):
            ref = pd.to_datetime(rec.ref_date, errors="coerce")
            if pd.isna(ref):
                paid = pd.to_datetime(rec.date, errors="coerce")
                dates = ex_by_ticker.get(rec.ticker)
                if pd.isna(paid) or dates is None or len(dates) == 0:
                    continue
                prior = dates[dates <= paid.to_datetime64()]
                if len(prior) == 0:
                    continue
                ref = pd.Timestamp(prior[-1])
            seen.add((rec.ticker, rec.account, ref.to_period("M").__str__()))

    rate_s = float(config["portfolio"]["tax_rate_specific"])
    rows = []
    for _, h in pos.iterrows():
        g = div[div["ticker"] == h["ticker"]]
        for _, r in g.iterrows():
            pay = (r["date"] + pd.Timedelta(days=lag_days)).normalize()
            if pay > today:
                continue
            key = (h["ticker"], h["account"], r["date"].to_period("M").__str__())
            if key in seen:
                continue
            gross = float(h["shares"]) * float(r["amount"])
            tax = 0.0 if h["account"] == "nisa" else gross * rate_s
            rows.append({
                "記録する": True,
                "コード": str(h.get("code") or h["ticker"][:-2]),
                "銘柄名": h.get("name"),
                "口座": "NISA" if h["account"] == "nisa" else "特定",
                "権利落ち日": r["date"].date(),
                "受取日": pay.date(),
                "株数": float(h["shares"]),
                "1株配当": float(r["amount"]),
                "税引前": round(gross, 0),
                "受取額（税引後）": round(gross - tax, 0),
                "_ticker": h["ticker"], "_account": h["account"],
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["受取日", "銘柄名"]).reset_index(drop=True)


def dividend_cash(days: int = 90) -> dict:
    """受け取った配当のうち、まだ買い付けに回していないぶん。

    いま年間 37万円ほど受け取っている。これは毎月の入金と同じ規模で、
    **再投資しなければ「育つ」にならない**。整理で生まれた余力と同じ仕組みで、
    受け取った配当も買い付けの原資として出す。

    受取は税引後で記録してあるので、そのまま使える額。
    配当の受取から buy を引くと、整理の売却代金と混ざってしまうので、
    ここでは **受け取った配当の合計だけ**を出し、使ったかどうかは
    ケンが画面で調整する。期間の既定を90日にしてあるのは、
    日本株の配当が年2回（6月・12月に集中）まとめて入るため。
    """
    tx = read_df(
        "SELECT date, shares, price FROM transactions "
        "WHERE type='dividend' AND date >= date('now', ?)", (f"-{int(days)} day",))
    if tx.empty:
        return {"受取額": 0.0, "件数": 0, "期間": int(days)}
    return {
        "受取額": float((tx["shares"] * tx["price"]).sum()),
        "件数": int(len(tx)),
        "期間": int(days),
    }


def freed_cash(days: int = 30) -> dict:
    """整理して生まれた、まだ使っていないお金。

    ケンの使い方は
        入金額を入れる → アプリが銘柄と株数を出す → 買う
        → 同時に整理すべきものを売る → 生まれた余力で同じことを繰り返す
    というループ。売ったあとに金額を手で足し算して打ち直すのでは続かないので、
    **売った手取りから、その後に買った額を引いた残り**を出す。
    買い付けを記録すれば自然にゼロへ戻るので、二重に使ってしまうことがない。

    手取りは税引後。特定口座では売却益に 20.315% かかるので、
    売却代金をそのまま次の買い付けに回すと金額が合わない。
    """
    tx = read_df(
        "SELECT date, type, shares, price, COALESCE(tax, 0) AS tax FROM transactions "
        "WHERE type IN ('buy','sell') AND date >= date('now', ?)", (f"-{int(days)} day",))
    if tx.empty:
        return {"手取り": 0.0, "使った額": 0.0, "残り": 0.0, "売却件数": 0, "買い付け件数": 0}
    amount = tx["shares"] * tx["price"]
    sells = tx["type"] == "sell"
    proceeds = float((amount[sells] - tx.loc[sells, "tax"]).sum())
    spent = float(amount[~sells].sum())
    return {
        "手取り": round(proceeds, 0),
        "使った額": round(spent, 0),
        "残り": round(max(proceeds - spent, 0.0), 0),
        "売却件数": int(sells.sum()),
        "買い付け件数": int((~sells).sum()),
    }
