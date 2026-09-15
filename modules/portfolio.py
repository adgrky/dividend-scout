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
        "trap_penalty, gate_passed, gate_reason FROM scores "
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


def review_candidates(pos: pd.DataFrame, config: dict) -> pd.DataFrame:
    """整理を検討すべき保有。判断はケンがする。ここは材料を並べるだけ。

    重さを分ける。「配当が危ない」と「保有額が小さい」を同じ扱いにすると、
    実測で114銘柄中99銘柄が候補になり、どれから手をつけるか分からなくなる。

    重大   配当そのものが危ない（減配・基準逸脱・継続スコアが低い）
    軽微   配当は問題ないが、額が小さく監視コストに見合わない
    """
    if pos.empty:
        return pos
    total = pos["eval_value"].sum()
    out = pos.copy()
    reasons, severities = [], []
    for _, r in out.iterrows():
        rs = []
        serious = False
        reason = r.get("gate_reason") if isinstance(r.get("gate_reason"), str) else ""
        # 財務をまだ取りに行っていないだけの銘柄を「基準を外れた」と書くと、
        # 保有114銘柄のうち110銘柄が整理候補になり、シグナルとして役に立たない。
        hard = [x for x in reason.split(" / ") if x and "財務未取得" not in x]
        if r.get("gate_passed") == 0 and hard:
            rs.append(f"基準を外れた（{' / '.join(hard)}）")
            serious = True
        # 保有の評価に発掘スコア（total）を使ってはいけない。あれは「市場に
        # 気づかれていないか」を含むので、大型株は構造的に低く出る（実測で
        # 三菱商事の見過ごされ度は8点）。配当が続くかだけを見た health で判定する。
        if pd.notna(r.get("health")) and r["health"] < 35:
            rs.append(f"配当継続スコアが低い（{r['health']:.0f}）")
            serious = True
        # ゲートは10年で減配1回まで許容している。1回を整理候補に挙げると
        # 採用基準と矛盾するので、ここも2回以上を対象にする。
        if (r.get("cuts_10y") or 0) >= 2:
            rs.append(f"10年で減配{int(r['cuts_10y'])}回")
            serious = True
        if pd.notna(r.get("streak_no_cut")) and r["streak_no_cut"] == 0:
            rs.append("直近で減配")
            serious = True
        if total and r["eval_value"] / total < 0.003:
            rs.append(f"保有額が小さい（全体の{r['eval_value'] / total:.1%}）")
        reasons.append(" / ".join(rs))
        severities.append("重大" if serious else ("軽微" if rs else ""))
    out["整理を検討する理由"] = reasons
    out["重さ"] = severities
    out = out[out["整理を検討する理由"] != ""].copy()
    # 重いものを上に、その中では金額の大きいものを上に
    out["_o"] = out["重さ"].map({"重大": 0, "軽微": 1}).fillna(2)
    return out.sort_values(["_o", "eval_value"], ascending=[True, False]).drop(columns="_o")


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
