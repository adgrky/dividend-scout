"""監視 — 保有と候補の異変を拾う。

発掘の裏返し。買ったあとに仮説が壊れていないかを機械が見張る。
アラートは「棄却条件に触れたか」を中心に組む。単に株価が下がったことは
アラートにしない（配当株では株価下落はむしろ買い場になる）。
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from modules.store import connect, read_df

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_alerts(config: dict) -> pd.DataFrame:
    """保有＋ウォッチリストを対象に異変を検出する。"""
    targets = read_df(
        "SELECT ticker FROM holdings UNION SELECT ticker FROM watchlist"
    )["ticker"].tolist()
    if not targets:
        return pd.DataFrame()

    placeholders = ",".join("?" * len(targets))
    sc = read_df(
        f"SELECT * FROM scores WHERE asof = (SELECT MAX(asof) FROM scores) "
        f"AND ticker IN ({placeholders})", tuple(targets)
    ).set_index("ticker")
    uni = read_df(f"SELECT ticker, name, sector33 FROM universe WHERE ticker IN ({placeholders})",
                  tuple(targets)).set_index("ticker")
    q = read_df(f"SELECT * FROM quotes WHERE ticker IN ({placeholders})",
                tuple(targets)).set_index("ticker")
    div = read_df(f"SELECT ticker, date, amount FROM dividends WHERE ticker IN ({placeholders})",
                  tuple(targets))
    watch = read_df("SELECT ticker, target_yield, bottom_yield, invalidation FROM watchlist"
                    ).set_index("ticker")
    hold = read_df("SELECT ticker, target_yield, bottom_yield FROM holdings"
                   ).groupby("ticker").first()

    from modules.dividend_history import build_profiles, profiles_to_frame
    prof = profiles_to_frame(build_profiles(div))

    targets_yield = watch[["target_yield", "bottom_yield"]].combine_first(hold)
    df = uni.join([q, sc, prof, targets_yield], how="left")
    df["dividend_yield"] = np.where(df["last_close"] > 0,
                                    df["dps_latest"] / df["last_close"], np.nan)

    now = datetime.now().isoformat(timespec="seconds")
    rows = []

    def add(ticker, severity, kind, message):
        rows.append({"detected_at": now, "ticker": ticker, "severity": severity,
                     "kind": kind, "message": message, "resolved": 0})

    for ticker, r in df.iterrows():
        name = r.get("name") or ticker

        # 1) 減配 — 配当株にとって最も重い異変
        if pd.notna(r.get("growth_latest")) and r["growth_latest"] < -0.001:
            add(ticker, "high", "dividend_cut",
                f"{name}: 配当が前年比 {r['growth_latest']:.1%}（{r['dps_prev']:.1f} → {r['dps_latest']:.1f}円）")

        # 2) ゲートから落ちた＝買う理由が消えた
        if r.get("gate_passed") == 0 and isinstance(r.get("gate_reason"), str) and r["gate_reason"]:
            add(ticker, "high", "gate_failed", f"{name}: 採用基準を外れた（{r['gate_reason']}）")

        # 3) トラップ検出
        penalty = r.get("trap_penalty") or 0
        if penalty >= 12:
            add(ticker, "medium", "trap", f"{name}: 高配当トラップの兆候（減点 {penalty:.0f}）")

        # 4) 指値到達 — 買い場。これは良い知らせのアラート
        ty = r.get("target_yield")
        if pd.notna(ty) and ty > 0 and pd.notna(r.get("dividend_yield")):
            if r["dividend_yield"] >= ty:
                price = r["last_close"]
                add(ticker, "low", "target_reached",
                    f"{name}: 目標利回り {ty:.2%} に到達（現在 {r['dividend_yield']:.2%} / ¥{price:,.0f}）")

        # 5) 据え置きが続いている＝増配ストーリーが止まった
        if pd.notna(r.get("streak")) and r["streak"] == 0 and (r.get("streak_no_cut") or 0) >= 2:
            add(ticker, "low", "dividend_flat", f"{name}: 増配が止まっている（据え置き）")

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_order"] = out["severity"].map(SEVERITY_ORDER)
    return out.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def save_alerts(alerts: pd.DataFrame) -> int:
    """同じ日に同じ内容を二重登録しない。"""
    if alerts is None or alerts.empty:
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    with connect() as conn:
        existing = {
            (r["ticker"], r["kind"])
            for r in conn.execute(
                "SELECT ticker, kind FROM alerts WHERE detected_at LIKE ?", (f"{today}%",)
            ).fetchall()
        }
        new = alerts[~alerts.apply(lambda r: (r["ticker"], r["kind"]) in existing, axis=1)]
        for _, r in new.iterrows():
            conn.execute(
                "INSERT INTO alerts (detected_at, ticker, severity, kind, message, resolved) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (r["detected_at"], r["ticker"], r["severity"], r["kind"], r["message"]),
            )
    return len(new)


def format_for_push(alerts: pd.DataFrame, limit: int = 10) -> str:
    """スマホ通知用の本文。長いと読まれないので重要なものだけ。"""
    if alerts is None or alerts.empty:
        return "異変なし"
    icons = {"high": "🔴", "medium": "🟡", "low": "🔵"}
    lines = [f"{icons.get(r['severity'], '・')} {r['message']}"
             for _, r in alerts.head(limit).iterrows()]
    if len(alerts) > limit:
        lines.append(f"…ほか {len(alerts) - limit} 件")
    return "\n".join(lines)
