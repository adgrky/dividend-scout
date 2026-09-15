"""検証7 — 現金を3割残して暴落で買い向かうのは、全部入れるより得か。

【なぜやるか】
ヘムの3本目の柱であり、このアプリの `market.cash_reserve: 0.30` と
「大人買いライン」はこの前提の上に立っている。**一度も測っていない。**

現金を寝かせるのはタダではない。相場が上がり続けた期間なら、寝かせたぶんだけ
確実に負ける。暴落で買い向かえる価値がそれを上回るかどうかは、やってみないと
分からない。

【やり方】
毎月おなじ額を入れる積み立てを、2005年から実際の相場で回す。

    A 全部入れる          … 毎月の入金を全額、その月に投入する
    B 7割入れて3割ためる   … 3割は現金に積む。指数が最高値から N% 下がったら、
                            ためた現金を段階的に投入する（アプリの大人買いラインと同じ形）
    C 何もためない・待つ   … 全額を現金に積み、下がったときだけ投入する（極端な例）

相場は、DBにある全銘柄の**等ウェイト・配当込み指数**で代表させる。
（日経平均PBRの過去データは手元に無いので、引き金は「最高値からの下落率」にする。
  ヘムのPBR0.8ラインに相当するのは、およそ最高値から30〜40%下の水準）

【この検証の限界】
DBにあるのは **いま上場している銘柄** なので、途中で消えた会社が入っていない
（生存者バイアス）。指数の水準そのものは実際より良く出る。ただしA・B・Cは
同じ指数を使うので、**比較としては意味がある**。

使い方:
    uv run python scripts/validate_cash_reserve.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                     # noqa: E402
import pandas as pd                    # noqa: E402

from modules import valreport as V     # noqa: E402
from modules.quality import trim_frame  # noqa: E402
from modules.store import read_df      # noqa: E402

MONTHLY = 100_000.0


def build_index() -> pd.Series:
    """全銘柄の等ウェイト・配当込みの月次指数。"""
    px, _ = trim_frame(read_df("SELECT ticker, date, close FROM prices"))
    px["date"] = pd.to_datetime(px["date"])
    px = px[px["close"] > 0]
    dv = read_df("SELECT ticker, date, amount FROM dividends")
    dv["date"] = pd.to_datetime(dv["date"])

    px["m"] = px["date"].dt.to_period("M")
    close = px.sort_values("date").groupby(["ticker", "m"])["close"].last().unstack(0)
    dv["m"] = dv["date"].dt.to_period("M")
    div = dv.groupby(["ticker", "m"])["amount"].sum().unstack(0).reindex(
        index=close.index, columns=close.columns).fillna(0.0)

    prev = close.shift(1)
    ret = (close + div) / prev - 1
    # 月に50銘柄以上の値が取れている月だけ使う
    ok = ret.notna().sum(axis=1) >= 50
    # 外れ値1銘柄で指数が壊れないよう、上下1%を刈る
    r = ret[ok].apply(lambda s: s.dropna().clip(s.dropna().quantile(0.01),
                                                s.dropna().quantile(0.99)).mean(), axis=1)
    return (1 + r).cumprod()


def simulate(idx: pd.Series, deploy_now: float, ladder: list[tuple[float, float]]) -> dict:
    """毎月 MONTHLY 円を入れる。deploy_now は即投入する割合。

    ladder は [(最高値からの下落率, ためた現金のうち投入する割合), ...]。
    深い段ほど多く入れる。一度使った段は同じ谷では二度使わない。
    """
    units, cash, invested = 0.0, 0.0, 0.0
    peak = 0.0
    used: set[int] = set()
    for dt, level in idx.items():
        peak = max(peak, level)
        dd = 1 - level / peak if peak > 0 else 0.0
        if dd < 0.05:
            used.clear()                      # 最高値圏に戻ったら段を戻す
        invested += MONTHLY
        now = MONTHLY * deploy_now
        cash += MONTHLY - now
        units += now / level
        for i, (trigger, share) in enumerate(ladder):
            if dd >= trigger and i not in used and cash > 0:
                use = cash * share
                units += use / level
                cash -= use
                used.add(i)
    value = units * idx.iloc[-1] + cash
    return {"投入した現金": invested, "最終評価額": value,
            "倍率": value / invested, "残った現金": cash,
            "現金の割合": cash / value}


def main() -> None:
    print("検証7 — 現金を3割残して暴落で買い向かうのは得か")
    idx = build_index()
    idx = idx[idx.index >= pd.Period("2005-01", "M")]
    print(f"  等ウェイト・配当込み指数: {idx.index[0]} 〜 {idx.index[-1]}"
          f"（{len(idx)} ヶ月）／ 期間の倍率 {idx.iloc[-1] / idx.iloc[0]:.2f}倍")
    dd = 1 - idx / idx.cummax()
    print(f"  いちばん深い下落 {dd.max():.1%}（{dd.idxmax()}）")
    print("  最高値から20%以上下げていた月の割合 "
          f"{(dd >= 0.20).mean():.1%}／30%以上 {(dd >= 0.30).mean():.1%}")

    ladder = [(0.10, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 1.00)]
    cases = {
        "A 全部入れる": 1.00,
        "B 8割入れて2割ためる": 0.80,
        "B' 7割入れて3割ためる（アプリの既定）": 0.70,
        "B'' 5割入れて5割ためる": 0.50,
        "C 全部ためて、下がったときだけ入れる": 0.00,
    }
    V.hr("1. 2005年から毎月10万円を積み立てたら")
    print(f"  {'やり方':<38s}{'最終評価額':>14s}{'倍率':>8s}{'現金比率':>10s}")
    base = None
    for name, d in cases.items():
        r = simulate(idx, d, ladder)
        if base is None:
            base = r["最終評価額"]
        print(f"  {name:<38s}{r['最終評価額']:>14,.0f}{r['倍率']:>8.2f}"
              f"{r['現金の割合']:>10.1%}   （Aとの差 {r['最終評価額'] / base - 1:>+6.1%}）")

    V.hr("2. 始めた時期を変えても同じか")
    starts = ["2005-01", "2007-01", "2010-01", "2013-01", "2016-01", "2019-01"]
    print(f"  {'開始':<10s}" + "".join(f"{k.split()[0]:>10s}" for k in cases))
    for st in starts:
        sub = idx[idx.index >= pd.Period(st, "M")]
        if len(sub) < 60:
            continue
        vals = [simulate(sub, d, ladder)["倍率"] for d in cases.values()]
        print(f"  {st:<10s}" + "".join(f"{v:>10.2f}" for v in vals))
    print("\n  どの開始時期でもAが勝つなら、現金を寝かせる理由は「成績」ではない。")

    V.hr("3. 現金を持つことで、何が得られるのか")
    for name, d in (("A 全部入れる", 1.00), ("B' 7割入れて3割ためる", 0.70)):
        units, cash, peak, worst = 0.0, 0.0, 0.0, 0.0
        used: set[int] = set()
        series = []
        for dt, level in idx.items():
            peak = max(peak, level)
            ddv = 1 - level / peak if peak > 0 else 0.0
            if ddv < 0.05:
                used.clear()
            now = MONTHLY * d
            cash += MONTHLY - now
            units += now / level
            for i, (trigger, share) in enumerate(ladder):
                if ddv >= trigger and i not in used and cash > 0:
                    use = cash * share
                    units += use / level
                    cash -= use
                    used.add(i)
            series.append(units * level + cash)
        s = pd.Series(series, index=idx.index)
        draw = (1 - s / s.cummax()).max()
        print(f"  {name:<28s} いちばん深い評価額の落ち込み {draw:>6.1%}")
    print("\n  現金の役割は成績ではなく、**落ち込みの浅さ**と、"
          "\n  そこで買い増せるという行動のしやすさ。")


if __name__ == "__main__":
    main()
