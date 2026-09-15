"""A層（増配余力）の検証。配当性向・営業CFの厚み・自己資本比率は効くのか。

【なぜ今まで検証していなかったか、そしてなぜ今できるのか】
yfinance の財務は4〜5期分しか取れないので、2014年や2016年時点の配当性向は
再現できない。だから「検証不能」として等ウェイトに置いていた。

しかし **EDINET の「主要な経営指標等の推移」には5年分入っている**。
実測すると FY2022 から約1,250社ぶん揃う。1コホート・3年先までなら測れる。
「検証不能」ではなく「1コホート・3年で測れる」が正しかった。

【設計】
    起点   2023-06-30（FY2022 の有価証券報告書が出そろう時期）
           先読みを避けるため、この日までに公表されている情報だけを使う
    実績   2023-06-30 → 2026-06-30 の3年間
           ・1株配当の伸び ・トータルリターン ・減配の有無

【この検証の弱さ（先に書いておく）】
    1. コホートが1つしかない。時期をまたいで安定しているか確かめられない
    2. 3年で、ほかの検証（5年）より短い
    3. 2023〜2026年は日本株が強かった時期。弱気相場では違う結果になりうる
    4. 母集団は EDINET を取得した1,267社＝粗いふるいを通った銘柄に偏っている
    これらを踏まえ、**結果が良くても重みを大きく上げない**。

使い方:
    uv run python scripts/validate_capacity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                    # noqa: E402
import pandas as pd                                   # noqa: E402
from scipy.stats import spearmanr                     # noqa: E402

from modules.store import read_df                     # noqa: E402
from modules.quality import trim_frame, winsorize     # noqa: E402

ASOF = pd.Timestamp("2023-06-30")
END = pd.Timestamp("2026-06-30")
BASE_FY = 2022


def _hr(t):
    print("\n" + "─" * 80 + f"\n{t}\n" + "─" * 80)


def build() -> pd.DataFrame:
    fin = read_df(
        "SELECT ticker, fiscal_year, net_income, eps, dps, roe, equity_ratio, operating_cf "
        "FROM edinet_summary WHERE fiscal_year = ?", (BASE_FY,))
    fin = fin[fin["eps"].notna() & (fin["eps"] != 0)]
    print(f"  FY{BASE_FY} の財務が取れた銘柄: {len(fin):,}")

    # 配当性向 = 1株配当 ÷ 1株利益。どちらも同じ有報の同じ年度なので、
    # 株式分割が調整されていなくても **比率は正しい**。
    fin["payout"] = fin["dps"] / fin["eps"]
    # 発行済株数を EPS から逆算し、配当総額に対する営業CFの厚みを出す
    fin["shares"] = fin["net_income"] / fin["eps"]
    fin["div_total"] = fin["dps"] * fin["shares"]
    fin["ocf_cover"] = np.where(fin["div_total"] > 0,
                                fin["operating_cf"] / fin["div_total"], np.nan)

    prices, _ = trim_frame(read_df("SELECT ticker, date, close FROM prices"))
    prices["date"] = pd.to_datetime(prices["date"])
    div = read_df("SELECT ticker, date, amount FROM dividends")
    div["date"] = pd.to_datetime(div["date"])

    rows = []
    for t, f in fin.set_index("ticker").iterrows():
        px = prices[prices["ticker"] == t].set_index("date")["close"].sort_index()
        dv = div[div["ticker"] == t].set_index("date")["amount"].sort_index()
        if px.empty or dv.empty:
            continue
        p0 = px[px.index <= ASOF]
        p1 = px[px.index <= END]
        if p0.empty or p1.empty or p1.index.max() < END - pd.Timedelta(days=45):
            continue
        price0, price1 = float(p0.iloc[-1]), float(p1.iloc[-1])
        if price0 <= 0:
            continue

        # 起点の1株配当（直前1年）と、3年後の1株配当（直前1年）
        d0 = float(dv[(dv.index > ASOF - pd.DateOffset(years=1)) & (dv.index <= ASOF)].sum())
        d1 = float(dv[(dv.index > END - pd.DateOffset(years=1)) & (dv.index <= END)].sum())
        if d0 <= 0:
            continue
        received = float(dv[(dv.index > ASOF) & (dv.index <= END)].sum())

        # 期間中に「前年より減った年」があったか
        had_cut = False
        for k in (1, 2, 3):
            a = float(dv[(dv.index > ASOF + pd.DateOffset(years=k - 1))
                         & (dv.index <= ASOF + pd.DateOffset(years=k))].sum())
            b = float(dv[(dv.index > ASOF + pd.DateOffset(years=k))
                         & (dv.index <= ASOF + pd.DateOffset(years=k + 1))].sum())
            if a > 0 and b > 0 and b < a * 0.999:
                had_cut = True
        rows.append({
            "ticker": t,
            "配当性向": f["payout"], "営業CFの厚み": f["ocf_cover"],
            "ROE": f["roe"], "自己資本比率": f["equity_ratio"],
            "起点の利回り": d0 / price0,
            "fwd_dps_growth": d1 / d0 - 1,
            "fwd_total_return": (price1 + received) / price0 - 1,
            "fwd_had_cut": int(had_cut),
        })
    return pd.DataFrame(rows)


def main() -> None:
    print(f"A層（増配余力）の検証  起点 {ASOF:%Y-%m-%d} → 実績 {END:%Y-%m-%d}（3年）")
    df = build()
    print(f"  実績まで追えた銘柄: {len(df):,}")
    if len(df) < 200:
        print("  少なすぎて判定できない")
        return

    factors = {
        "A_配当性向の低さ": -df["配当性向"],
        "A_営業CFの厚み": df["営業CFの厚み"],
        "A_自己資本比率": df["自己資本比率"],
        "A_ROE": df["ROE"],
        "（比較）起点の利回り": df["起点の利回り"],
    }
    outcomes = {"DPS成長（3年）": "fwd_dps_growth",
                "トータルリターン（3年）": "fwd_total_return",
                "減配の発生": "fwd_had_cut"}

    _hr("1. 各指標と3年後の実績の順位相関")
    tbl = {}
    for fname, fv in factors.items():
        tbl[fname] = {}
        for oname, ocol in outcomes.items():
            m = fv.notna() & df[ocol].notna()
            if m.sum() < 100:
                tbl[fname][oname] = np.nan
                continue
            tbl[fname][oname] = spearmanr(fv[m], df.loc[m, ocol]).statistic
    t = pd.DataFrame(tbl).T.round(3)
    print(t.to_string())
    print("\n目安: |相関| < 0.05 はノイズ。0.10 を超えたら意味がある可能性がある。")

    _hr("2. 分位別の実績（5分位・中央値）")
    for fname, fv in factors.items():
        m = fv.notna()
        if m.sum() < 300:
            continue
        q = pd.qcut(fv[m].rank(method="first"), 5, labels=[f"Q{i}" for i in range(1, 6)])
        g = df[m].groupby(q, observed=True).agg(
            n=("ticker", "count"),
            DPS成長=("fwd_dps_growth", "median"),
            リターン=("fwd_total_return", "median"),
            減配率=("fwd_had_cut", "mean"))
        print(f"\n  【{fname}】（Q5が最上位）")
        line_d = "  ".join(f"{i}:{v:+.0%}" for i, v in zip(g.index, g["DPS成長"]))
        line_r = "  ".join(f"{i}:{v:+.0%}" for i, v in zip(g.index, g["リターン"]))
        line_c = "  ".join(f"{i}:{v:.0%}" for i, v in zip(g.index, g["減配率"]))
        print(f"    DPS成長  {line_d}")
        print(f"    リターン {line_r}")
        print(f"    減配率   {line_c}")

    _hr("3. 配当性向のスイートスポット（30〜50%が最高という前提は正しいか）")
    d = df[df["配当性向"].between(-0.5, 3.0)].copy()
    bins = [-np.inf, 0.15, 0.30, 0.50, 0.70, 1.00, np.inf]
    labels = ["15%未満", "15-30%", "30-50%", "50-70%", "70-100%", "100%超"]
    d["帯"] = pd.cut(d["配当性向"], bins, labels=labels)
    g = d.groupby("帯", observed=True).agg(
        n=("ticker", "count"), DPS成長=("fwd_dps_growth", "median"),
        リターン=("fwd_total_return", "median"), 減配率=("fwd_had_cut", "mean"),
        起点の利回り=("起点の利回り", "median"))
    for c in ("DPS成長", "リターン", "減配率", "起点の利回り"):
        g[c] = (g[c] * 100).round(1)
    print(g.to_string())

    _hr("4. 高利回り × 減配歴なし に、増配余力を重ねたら減配は減るか")
    hi = df["起点の利回り"] >= df["起点の利回り"].quantile(0.7)
    ok_payout = df["配当性向"].between(0.0, 0.5)
    ok_ocf = df["営業CFの厚み"] >= 3
    for label, m in (("全体", pd.Series(True, index=df.index)),
                     ("高利回り上位30%", hi),
                     ("  ＋ 配当性向 50%以下", hi & ok_payout),
                     ("  ＋ 営業CFが配当の3倍以上", hi & ok_payout & ok_ocf)):
        s = df[m]
        if len(s) < 30:
            print(f"  {label:<30s} n={len(s):>5,}  （少なすぎる）")
            continue
        print(f"  {label:<30s} n={len(s):>5,}  減配率 {s['fwd_had_cut'].mean():>5.1%}"
              f"  リターン {s['fwd_total_return'].median():>+7.1%}"
              f"  DPS成長 {s['fwd_dps_growth'].median():>+7.1%}")

    _hr("この検証の弱さ")
    print("""  1. コホートが1つしかない。時期をまたいで安定しているか確かめられない
  2. 3年で、ほかの検証（5年）より短い
  3. 2023〜2026年は日本株が強かった時期。弱気相場では違う結果になりうる
  4. 母集団は EDINET を取得した1,267社＝粗いふるいを通った銘柄に偏っている

  → **結果が良くても、重みを大きく上げない。** 効いた向きだけを見る。""")


if __name__ == "__main__":
    main()
