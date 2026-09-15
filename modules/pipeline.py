"""発掘パイプラインの中核。Streamlit に一切依存しない。

app.py はこれを @st.cache_data で包むだけ、GitHub Actions は同じ関数を直接呼ぶ。
こうしておかないと「画面で見た結果」と「通知が使った結果」がズレる。

流れ:
    build_feature_table(stage=1)  価格・配当だけで作れる特徴量 → 粗いふるい
    fetch_fundamentals()          絞った銘柄だけ財務を取る（遅いので必ず絞る）
    build_feature_table(stage=2)  財務を足した完全な特徴量
    run_scoring()                 ゲート → トラップ → スコア → DB 保存
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable

import numpy as np
import pandas as pd

from modules import fundamentals as fnd
from modules import scoring, traps
from modules.dividend_history import build_profiles, profiles_to_frame
from modules.store import read_df, upsert_df
from modules.quality import trim_frame
from modules.valuation import build_valuation_table

ProgressFn = Callable[[float, str], None]

_SCORE_COLS = ["ticker", "asof", "total", "capacity", "willingness", "growth",
               "neglect", "valuation", "trap_penalty", "gate_passed", "gate_reason",
               "detail_json"]


def _safe_div(a, b):
    b = pd.Series(b).replace(0, np.nan)
    return pd.Series(a).astype(float) / b.astype(float)


def load_base() -> pd.DataFrame:
    """ユニバース＋最新値＋配当プロフィール＋利回りパーセンタイルを結合する。"""
    uni = read_df("SELECT ticker, code, name, sector33, market FROM universe").set_index("ticker")
    quotes = read_df("SELECT * FROM quotes").set_index("ticker")
    div = read_df("SELECT ticker, date, amount FROM dividends")
    prices, trimmed = trim_frame(read_df("SELECT ticker, date, close FROM prices"))
    if not trimmed.empty:
        print(f"  価格データの破損区間を除外: {len(trimmed)} 銘柄")

    prof = profiles_to_frame(build_profiles(div))
    val = build_valuation_table(prices, div)

    df = uni.join(quotes, how="left").join(prof, how="left").join(
        val[["percentile", "median", "price_at_median_yield", "current"]]
        .rename(columns={"percentile": "yield_percentile", "median": "yield_median",
                         "current": "yield_ttm"}), how="left")

    today = pd.Timestamp(date.today())
    df["listing_years"] = (today - pd.to_datetime(df["listing_start"])).dt.days / 365.25
    df["avg_turnover_man"] = df["avg_turnover"] / 1e4
    # 直近12ヶ月の実績配当をそのまま使う（会社予想は yfinance から安定して取れない）
    df["dividend_yield"] = _safe_div(df["dps_latest"], df["last_close"]).values
    return df


def prescreen(df: pd.DataFrame, config: dict) -> pd.Index:
    """財務を取りに行く前の粗いふるい。

    ここで落とすのは、財務を見るまでもなく対象外と分かるものだけにする。
    財務が必要な条件（自己資本比率・配当性向など）はここでは判定しない。
    """
    g = config["gate"]
    ok = (
        (df["listing_years"] >= g["min_listing_years"])
        & (df["avg_turnover_man"] >= g["min_avg_turnover_man"])
        & (df["years_paying"] >= g["min_dividend_years"])
        & (df["cuts_10y"].fillna(99) <= g["max_dividend_cuts_10y"])
        & (df["dps_latest"].fillna(0) > 0)
    )
    return df.index[ok.fillna(False)]


def fetch_fundamentals(tickers, progress: ProgressFn | None = None,
                       strict: bool = True) -> None:
    """財務を取って DB に入れる。

    レート制限で中断された場合も、そこまでに取れたぶんは保存してから
    例外を投げ直す。取り直しは --missing-only で差分だけ流せばいい。
    """
    try:
        fund, snap = fnd.fetch_many(tickers, progress=progress, strict=strict)
    except fnd.RateLimited as exc:
        # 中断までに取れたぶんは保存してから投げ直す。捨てると次回また同じ銘柄を叩く。
        if exc.partial_fund is not None and not exc.partial_fund.empty:
            upsert_df("fundamentals", exc.partial_fund, fnd.FUNDAMENTAL_COLS)
        if exc.partial_snap is not None and not exc.partial_snap.empty:
            upsert_df("snapshots", exc.partial_snap, fnd.SNAPSHOT_COLS)
        raise
    if not fund.empty:
        upsert_df("fundamentals", fund, fnd.FUNDAMENTAL_COLS)
    if not snap.empty:
        upsert_df("snapshots", snap, fnd.SNAPSHOT_COLS)


def attach_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    fund = read_df("SELECT * FROM fundamentals")
    metrics = fnd.latest_metrics(fund)
    snap = read_df(
        "SELECT s.* FROM snapshots s JOIN "
        "(SELECT ticker, MAX(asof) m FROM snapshots GROUP BY ticker) x "
        "ON s.ticker = x.ticker AND s.asof = x.m"
    ).set_index("ticker").drop(columns=["asof"], errors="ignore")

    out = df.join(metrics, how="left", rsuffix="_f").join(snap, how="left", rsuffix="_s")

    shares = out["shares"]
    div_total = out["dps_latest"] * shares
    out["market_cap_oku"] = out["market_cap"] / 1e8
    out["payout_ratio"] = _safe_div(div_total, out["net_income"]).values
    out["payout_ratio_prev"] = _safe_div(out["dps_prev"] * shares, out["net_income_prev"]).values
    out["fcf_payout_ratio"] = _safe_div(div_total, out["free_cf"]).values
    out["fcf_cover"] = _safe_div(out["free_cf"], div_total).values
    out["net_cash_ratio"] = _safe_div(out["net_cash"], out["market_cap"]).values
    # ネットキャッシュなら負債負担ゼロ扱い
    net_debt = (-out["net_cash"]).clip(lower=0)
    out["net_debt_to_ocf"] = _safe_div(net_debt, out["operating_cf"]).fillna(0.0).values
    out["operating_margin"] = _safe_div(out["operating_income"], out["revenue"]).values
    # ヘム指数 = 配当利回り × 10 ÷ 配当性向。1.0 超で「配当性向 < 利回り×10」を満たす。
    # 高利回りと低配当性向を同時に要求する一本の式で、PER < 10 と数学的に同値。
    # 利回り単独で買うと配当が育たない（検証: DPS 5年 -3.8%）のは、配当性向の高い
    # 銘柄が混ざるため。この指数はその2つを分離せずに評価する。
    out["hem_ratio"] = _safe_div(out["dividend_yield"] * 10, out["payout_ratio"]).values

    # 赤字・営業CFマイナスは配当性向が負になって「性向が低い＝優秀」と誤読される。
    # 負の値は判定不能として落とす（ゲートで別途落ちる）。
    for col in ("payout_ratio", "fcf_payout_ratio", "payout_ratio_prev"):
        out[col] = out[col].where(out[col] >= 0)
    return out


def run_scoring(config: dict, asof: str | None = None,
                progress: ProgressFn | None = None) -> pd.DataFrame:
    """特徴量 → ゲート → トラップ → スコア → DB 保存。"""
    asof = asof or date.today().isoformat()
    if progress:
        progress(0.1, "特徴量を組み立て中...")
    df = attach_fundamentals(load_base())

    if progress:
        progress(0.5, "ゲート判定中...")
    gated = scoring.apply_gate(df, config)

    if progress:
        progress(0.7, "トラップ検出中...")
    penalty, labels = traps.detect(gated, config)

    if progress:
        progress(0.85, "スコア計算中...")
    scores = scoring.compute_scores(gated, config, penalty, labels)

    out = gated[["gate_passed", "gate_reason"]].copy()
    out["gate_passed"] = out["gate_passed"].astype(int)
    for col in ["total", "capacity", "willingness", "growth", "neglect", "valuation",
                "trap_penalty", "detail_json"]:
        out[col] = scores[col] if col in scores.columns else None
    out["asof"] = asof
    out = out.reset_index().rename(columns={"index": "ticker"})
    upsert_df("scores", out, _SCORE_COLS)

    if progress:
        n = int(out["gate_passed"].sum())
        progress(1.0, f"完了：ゲート通過 {n} / {len(out)} 銘柄")
    return gated.join(scores[["total"] + scoring.LAYERS + ["trap_penalty"]], how="left")


def latest_scores() -> pd.DataFrame:
    """最新スナップショットのスコアをユニバース情報つきで返す。"""
    return read_df("""
        SELECT s.*, u.name, u.sector33, u.market, u.code,
               q.last_close, q.pos_52w, q.avg_turnover
        FROM scores s
        JOIN universe u ON u.ticker = s.ticker
        LEFT JOIN quotes q ON q.ticker = s.ticker
        WHERE s.asof = (SELECT MAX(asof) FROM scores)
    """)
