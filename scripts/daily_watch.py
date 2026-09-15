"""平日朝の監視。保有＋ウォッチリストだけを見る。

全市場は週1回でいい。毎日見るべきなのは「すでに持っているもの」と
「買おうとしているもの」だけ。

使い方:
    uv run python scripts/daily_watch.py --dry-run   # 通知内容を画面に出すだけ
    uv run python scripts/daily_watch.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import notifier                                      # noqa: E402
from modules.bulk_fetch import scan                               # noqa: E402
from modules.config import load_config                            # noqa: E402
from modules.monitor import build_alerts, format_for_push, save_alerts  # noqa: E402
from modules.pipeline import fetch_fundamentals, run_scoring      # noqa: E402
from modules.store import init_db, read_df, upsert_df             # noqa: E402

_QUOTE_COLS = ["ticker", "asof", "last_close", "high_52w", "low_52w", "pos_52w",
               "avg_turnover", "ret_1y", "listing_start", "n_bars"]


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="通知を送らず画面に出すだけ")
    ap.add_argument("--skip-fetch", action="store_true", help="株価取得を飛ばす")
    ap.add_argument("--with-fundamentals", action="store_true",
                    help="財務も取り直す（週1回で十分なので通常は不要）")
    args = ap.parse_args()

    config = load_config()
    init_db()

    targets = read_df("SELECT ticker FROM holdings UNION SELECT ticker FROM watchlist"
                      )["ticker"].tolist()
    if not targets:
        _log("保有もウォッチリストも空。何もしない。")
        return
    _log(f"対象 {len(targets)} 銘柄")

    if not args.skip_fetch:
        res = scan(targets, config, progress=lambda p, m: _log(f"  {p:5.1%} {m}"))
        upsert_df("prices", res["prices"], ["ticker", "date", "close", "volume"])
        upsert_df("dividends", res["dividends"], ["ticker", "date", "amount"])
        upsert_df("quotes", res["quotes"], _QUOTE_COLS)
        _log("株価・配当を更新")

    if args.with_fundamentals:
        fetch_fundamentals(targets, progress=lambda p, m: _log(f"  {p:5.1%} {m}"))

    run_scoring(config, progress=lambda p, m: _log(f"  {p:5.1%} {m}"))

    alerts = build_alerts(config)
    body = format_for_push(alerts)
    _log(f"検出 {len(alerts)} 件")
    print("─" * 60)
    print(body)
    print("─" * 60)

    if args.dry_run:
        _log("--dry-run のため通知は送らない")
        return

    n = save_alerts(alerts)
    _log(f"{n} 件を新規記録")
    if n > 0:
        high = int((alerts["severity"] == "high").sum())
        notifier.send(
            title=f"配当ポートフォリオ 監視（重大 {high} 件）",
            message=body,
            priority="high" if high else "default",
            tags="warning" if high else "chart_with_upwards_trend",
        )


if __name__ == "__main__":
    main()
