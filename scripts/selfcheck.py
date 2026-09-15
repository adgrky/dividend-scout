"""外部データ源の健全性チェック。DB を必要としない。

JPX の一覧URLは過去に data_j.xls → data_j.xlsx で404になったことがあり、
週次スキャンが動いてから気づくのでは遅い。事前に見張る。

使い方:
    uv run python scripts/selfcheck.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.config import load_config        # noqa: E402
from modules.universe import fetch_universe   # noqa: E402

_PROBE = ["9432.T", "8058.T", "2914.T"]


def main() -> None:
    failures: list[str] = []
    config = load_config()

    # 1) JPX 上場銘柄一覧
    try:
        uni = fetch_universe(config["universe"]["markets"])
        if len(uni) < 3000:
            failures.append(f"JPX の銘柄数が少なすぎる: {len(uni)}")
        elif uni["sector33"].nunique() < 30:
            failures.append(f"33業種区分が揃っていない: {uni['sector33'].nunique()} 種")
        else:
            print(f"OK  JPX: {len(uni)} 銘柄 / {uni['sector33'].nunique()} 業種")
    except Exception as exc:
        failures.append(f"JPX の取得に失敗: {exc}")

    # 2) yfinance の一括取得（株価・配当・分割が揃って返るか）
    try:
        from modules.bulk_fetch import download_batch
        frames = download_batch(_PROBE, period="10y")
        if len(frames) < len(_PROBE):
            failures.append(f"yfinance の一括取得が欠けた: {len(frames)}/{len(_PROBE)} 銘柄")
        for t, df in frames.items():
            if "Dividends" not in df.columns:
                failures.append(f"{t}: Dividends 列が返らない")
            elif int((df["Dividends"] > 0).sum()) < 10:
                failures.append(f"{t}: 配当イベントが少なすぎる（{int((df['Dividends'] > 0).sum())} 件）")
            elif df["Close"].isna().all():
                failures.append(f"{t}: Close が全て欠損")
        print(f"OK  yfinance: {len(frames)} 銘柄の株価・配当・分割を取得")
    except Exception as exc:
        failures.append(f"yfinance の取得に失敗: {exc}")

    # 3) 財務（4〜5期しか返らない前提が崩れていないか）
    try:
        from modules.fundamentals import fetch_one
        fund, _ = fetch_one("9432.T")
        if fund.empty:
            failures.append("財務諸表が取得できない")
        else:
            print(f"OK  財務: {len(fund)} 期分 / 最新 {fund['fiscal_end'].max()}")
    except Exception as exc:
        failures.append(f"財務の取得に失敗: {exc}")

    if failures:
        print("\n問題あり:")
        for f in failures:
            print(f"   - {f}")
        raise SystemExit(1)
    print("\nすべて正常")


if __name__ == "__main__":
    main()
