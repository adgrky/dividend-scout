"""スコアの予測力を過去データで確かめる（Phase 0.5）。

重みを決める前に必ずこれを通す。効かなかった層の重みは上げない。

使い方:
    uv run python scripts/validate_score.py
    uv run python scripts/validate_score.py --cohorts 2014,2016,2018,2020
    uv run python scripts/validate_score.py --out data/validation.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.store import read_df                                    # noqa: E402
from modules.validation import (Cohort, OUTCOMES, TESTABLE_FACTORS,  # noqa: E402
                                benchmark, composite_test, factor_power,
                                quintile_table, run_cohort)

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)


def _hr(title: str) -> None:
    print("\n" + "─" * 78)
    print(title)
    print("─" * 78)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohorts", default="2014,2016,2018,2020",
                    help="起点にする年（12月末時点）をカンマ区切りで")
    ap.add_argument("--horizon", type=int, default=5, help="先の年数")
    ap.add_argument("--out", default=None, help="コホート結合結果の保存先 CSV")
    ap.add_argument("--from-csv", default=None,
                    help="--out で保存した CSV から読み直す（コホート構築をやり直さない）")
    args = ap.parse_args()

    if args.from_csv:
        allc = pd.read_csv(args.from_csv)
        frames = {int(str(a)[:4]): g for a, g in allc.groupby("asof")}
        print(f"読み込み: {args.from_csv} / {len(frames)} コホート / 延べ {len(allc):,} 銘柄")
        _report(allc, frames, args)
        return

    print("株価と配当履歴を読み込み中...")
    prices = read_df("SELECT ticker, date, close, volume FROM prices ORDER BY ticker, date")
    div = read_df("SELECT ticker, date, amount FROM dividends")
    if prices.empty or div.empty:
        print("❌ データがない。先に scripts/weekly_scan.py を走らせること。")
        raise SystemExit(1)
    print(f"  {prices['ticker'].nunique():,} 銘柄 / 週足 {len(prices):,} 行 / 配当 {len(div):,} 行")

    years = [int(y) for y in args.cohorts.split(",")]
    frames = {}
    for y in years:
        asof = pd.Timestamp(f"{y}-12-31")
        print(f"\nコホート {y}年末 を構築中（{args.horizon}年先の実績と突き合わせ）...")
        df = run_cohort(prices, div, Cohort(asof, args.horizon))
        if df.empty:
            print("  → 銘柄数が足りないためスキップ")
            continue
        frames[y] = df
        print(f"  → {len(df):,} 銘柄")

    if not frames:
        print("❌ 有効なコホートが1つも作れなかった")
        raise SystemExit(1)

    allc = pd.concat(frames.values(), ignore_index=True)
    if args.out:
        allc.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n保存: {args.out}")
    _report(allc, frames, args)


def _report(allc: pd.DataFrame, frames: dict, args) -> None:
    # ── 1. 指標ごとの説明力 ──
    _hr("1. 各指標と5年後の実績の順位相関（全コホート結合）")
    power = factor_power(allc)
    pivot = power.pivot(index="指標", columns="実績", values="相関").round(3)
    print(pivot.to_string())
    print("\n目安: |相関| < 0.05 はノイズ。0.10 を超えたら意味がある可能性がある。")
    strong = power[power["相関"].abs() >= 0.10]
    if strong.empty:
        print("⚠️  0.10 を超えた指標は1つもない。これは stock-recommender と同じ結論であり、"
              "スコアで銘柄を選べるという前提そのものを疑う必要がある。")
    else:
        print("\n0.10 を超えた組み合わせ:")
        print(strong.sort_values("相関", key=abs, ascending=False).to_string(index=False))

    # ── 2. コホート別（安定して効いているか）──
    _hr("2. コホート別の相関（時期をまたいで安定しているか）")
    rows = []
    for y, df in frames.items():
        p = factor_power(df)
        p["コホート"] = y
        rows.append(p)
    by_cohort = pd.concat(rows)
    for oname in OUTCOMES:
        sub = by_cohort[by_cohort["実績"] == oname]
        if sub.empty:
            continue
        print(f"\n【{oname}】")
        print(sub.pivot(index="指標", columns="コホート", values="相関").round(3).to_string())
    print("\n符号がコホートごとに入れ替わる指標は、効いていないと考えるべき。")

    # ── 3. 分位別 ──
    _hr("3. 分位別の実績（相関が小さくても上位だけ効くことがある）")
    for label, col in TESTABLE_FACTORS.items():
        t = quintile_table(allc, col, "fwd_total_return")
        if t.empty:
            continue
        line = "  ".join(f"{r['分位']}:{r['中央値']:+.0%}" for _, r in t.iterrows())
        print(f"{label:<28s} {line}")
    print("\n（Q5 が最上位。中央値で表示。Q1→Q5 で単調に上がっていれば本物の可能性がある）")

    # ── 4. ベンチマーク ──
    _hr("4. ベンチマーク（これに勝てないスコアは採用しない）")
    bench = benchmark(allc)
    for col in ("リターン中央値", "リターン平均（裾を刈る）", "DPS成長 中央値", "減配発生率"):
        bench[col] = bench[col].map(lambda v: f"{v:+.1%}" if pd.notna(v) else "—")
    print(bench.to_string(index=False))

    # ── 5. 前半で決めて後半で答え合わせ ──
    _hr("5. 前半コホートで決めて、後半コホートで答え合わせ")
    ys = sorted(frames)
    if len(ys) < 2:
        print("コホートが1つしかないので分割できない")
    else:
        half = len(ys) // 2 or 1
        train_years, test_years = ys[:half], ys[half:]
        train = pd.concat([frames[y] for y in train_years], ignore_index=True)
        test = pd.concat([frames[y] for y in test_years], ignore_index=True)
        print(f"学習: {train_years}（{len(train):,} 銘柄） / 検証: {test_years}（{len(test):,} 銘柄）")
        for outcome in ("fwd_total_return", "fwd_dps_growth"):
            print(f"\n【{ {v: k for k, v in OUTCOMES.items()}[outcome] }】")
            res = composite_test(train, test, outcome=outcome)
            for _, r in res.iterrows():
                for k, v in r.items():
                    print(f"  {k}: {v:.1%}" if isinstance(v, float) else f"  {k}: {v}")

    _hr("結論の読み方")
    print("""検証できたのは、株価と配当履歴から過去時点を再現できる層だけ:
    B 増配意思 / E 割安 / D 見過ごされ度（売買代金の薄さのみ）

  A 増配余力 と C 原資成長 は、yfinance の財務が4〜5期分しか取れないため
  過去時点を再現できず、**検証不能**。効くかどうか分からない層の重みを
  上げてはいけないので、EDINET を入れる Phase 3 まで等ウェイトで据え置く。

  config.yaml の weights を動かすのは、上の1〜5で一貫して効いた層だけにすること。""")


if __name__ == "__main__":
    main()
