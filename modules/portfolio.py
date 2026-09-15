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


def review_candidates(pos: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    ※ 使っていない。modules/sell_rules.evaluate に置き換え済み（買う基準を売る基準に流用していたため）。
整理を検討すべき保有。判断はケンがする。ここは材料を並べるだけ。

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
