"""「ほっといた場合」の比較対象を取り込む。

自分で選んだ結果が、指数をただ買って放っておいた場合より良かったのかを見るための
データ。対照を置かずに自分の成績だけ見ると、相場が上がっただけの期間を実力だと
取り違える。

分配金は再投資した扱い（auto_adjust=True）で取る。配当を出す指数を
「値動きだけ」で比べると、配当を出している側が一方的に不利になる。

使い方:
    uv run python scripts/fetch_benchmarks.py
    uv run python scripts/fetch_benchmarks.py --period 5y
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.config import bridge_secrets_to_env    # noqa: E402
from modules.store import connect, init_db, upsert_df   # noqa: E402

# 指数そのものは買えないので、実際に買えるETFで比べる。
# 1306 は TOPIX連動で、自分の持ち株（日本の高配当株）に対する素直な対照。
BENCHMARKS = {
    "1306.T": "TOPIX（ETF）",
    "1577.T": "日本高配当株70（ETF）",
    "1489.T": "日経高配当株50（ETF）",
}


def drop_spikes(close: pd.Series, tol: float = 0.4) -> tuple[pd.Series, list]:
    """前後の水準から極端に外れた終値を落とす。

    yfinance は分割の前後で、桁の違う終値を数日ぶん返すことがある
    （実測: 1306.T の 2026-03-30・03-31 が 375円 のところ 36.9円、ちょうど10分の1で
    返ってきた。翌営業日からは元の水準に戻る）。そのまま比較に使うと、その2日だけ
    -90% の谷ができて他の差がまったく読めなくなる。

    前後21日の中央値と比べて 40% 以上ずれている日を落とす。分配金を再投資した値
    （auto_adjust）で取っているので、本来この系列に段差は出ない。指数のETFが
    3週間の中央値から4割ずれることは実際には起きないので、ずれたら取り込みの事故。

    隣の日とだけ比べる方法では足りない。上の事故は2日続いたので、
    「1日だけ跳ねて戻る」という形では捕まらなかった。
    """
    if len(close) < 5:
        return close, []
    med = close.rolling(21, center=True, min_periods=5).median()
    ratio = close / med
    bad = ((ratio < 1 - tol) | (ratio > 1 / (1 - tol))) & med.notna()
    bad = bad.fillna(False)
    return close[~bad], list(close.index[bad])


def fetch(symbol: str, period: str) -> pd.DataFrame:
    import yfinance as yf
    tk = yf.Ticker(symbol)
    df = tk.history(period=period, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    close = df["Close"].astype(float).dropna()
    close, dropped = drop_spikes(close)
    for d in dropped:
        print(f"    ⚠️ {symbol} {d:%Y-%m-%d} の終値がおかしいので外しました")
    out = pd.DataFrame({
        "symbol": symbol,
        "date": pd.to_datetime(close.index).tz_localize(None).strftime("%Y-%m-%d"),
        "close": close.values,
    })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="10y", help="取り込む期間（既定 10y）")
    args = ap.parse_args()

    bridge_secrets_to_env()
    init_db()

    total = 0
    for symbol, label in BENCHMARKS.items():
        try:
            df = fetch(symbol, args.period)
        except Exception as e:
            print(f"  失敗: {label}（{symbol}）— {e}")
            continue
        if df.empty:
            print(f"  取れませんでした: {label}（{symbol}）")
            continue
        # 前に取り込んだおかしな値を消してから入れ直す。上書きだけだと、
        # 今回外した日の古い値がそのまま残ってしまう。
        with connect() as conn:
            conn.execute("DELETE FROM benchmarks WHERE symbol=? AND date BETWEEN ? AND ?",
                         (symbol, df["date"].min(), df["date"].max()))
        n = upsert_df("benchmarks", df, ["symbol", "date", "close"])
        total += n
        print(f"  {label}（{symbol}）: {n} 日ぶん（{df['date'].min()}〜{df['date'].max()}）")
        time.sleep(1)   # 連続で叩くと弾かれる

    print(f"\n合計 {total} 行を保存しました。")


if __name__ == "__main__":
    main()
