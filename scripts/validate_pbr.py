"""検証17 — 「日経平均PBR 0.8」は、現実に来る水準なのか。

【なぜやるか】
ヘムの3本目の柱は「日経平均PBR 0.8 を底値目安に、10分割で買い向かう」。
アプリの大人買いラインもこれを写している。だが **PBR 0.8 が過去に何回来たのか、
いまの 1.86 からそこまで何%の下落なのかを、一度も確かめていない。**

日経平均PBRの過去データは日経のサイトが当月ぶんしか出さず（アーカイブはCloudflareで
取れない）、JPX も直接は落とせない。そこで**自分のデータで市場全体のPBRを組む**。

    市場PBR ＝ Σ(時価総額) ÷ Σ(純資産)
        時価総額 ＝ その月末の株価 × 株数
        株数     ＝ 有報の 純利益 ÷ 1株利益（分割の枠をそろえたもの）
        純資産   ＝ 有報の値

日経平均PBR とは構成銘柄も加重も違うので**水準はずれる**。だが「いまが過去の中で
どこか」「下がるときどこまで下がるか」は同じように動くはず。そこを見る。

有報が遡れるのは FY2013 まで。つまり **2014年以降しか作れない**。
リーマン（2009）や民主党政権末期（2012）の底は、この方法では見えない。
そこは素直に「測れない」と書く。

使い方:
    uv run python scripts/validate_pbr.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                           # noqa: E402
import pandas as pd                          # noqa: E402

from modules import valreport as V           # noqa: E402
from modules.config import load_config       # noqa: E402
from modules.hist_panel import load_cached   # noqa: E402
from modules.sim import monthly_panel        # noqa: E402


def build_market_pbr() -> pd.DataFrame:
    p = load_cached()
    p = p[p["net_assets"].notna() & (p["net_assets"] > 0) & p["shares"].notna()
          & (p["shares"] > 0) & p["fy_end"].notna()].copy()
    # その年度の数字が世に出る日（期末の3ヶ月後）から、次の年度が出るまで使う
    p["from"] = (p["fy_end"] + pd.DateOffset(months=3)).dt.to_period("M")
    panel = monthly_panel()
    close = panel["close"]

    rows = []
    for m in close.index:
        if m < pd.Period("2014-07", "M"):
            continue
        avail = p[p["from"] <= m]
        if avail.empty:
            continue
        latest = avail.sort_values("from").groupby("ticker").tail(1)
        px = close.loc[m]
        latest = latest[latest["ticker"].isin(px.index)]
        prices = px.reindex(latest["ticker"]).to_numpy(float)
        ok = np.isfinite(prices) & (prices > 0)
        if ok.sum() < 500:
            continue
        cap = (prices[ok] * latest["shares"].to_numpy(float)[ok]).sum()
        eq = latest["net_assets"].to_numpy(float)[ok].sum()
        rows.append({"month": m, "銘柄数": int(ok.sum()), "PBR": cap / eq})
    return pd.DataFrame(rows).set_index("month")


def main() -> None:
    print("検証17 — 日経平均PBR 0.8 は現実に来る水準なのか")
    cfg = load_config()
    d = build_market_pbr()
    if d.empty:
        print("  作れませんでした")
        return
    print(f"  自分のデータで組んだ市場PBR: {d.index[0]} 〜 {d.index[-1]}"
          f"（{len(d)} ヶ月、平均 {d['銘柄数'].mean():.0f} 銘柄）")

    V.hr("1. 12年ぶんの市場PBRは、どこからどこまで動いたか")
    print(f"  いちばん低い  {d['PBR'].min():.2f}（{d['PBR'].idxmin()}）")
    print(f"  中央値       {d['PBR'].median():.2f}")
    print(f"  いちばん高い  {d['PBR'].max():.2f}（{d['PBR'].idxmax()}）")
    print(f"  直近         {d['PBR'].iloc[-1]:.2f}（{d.index[-1]}）")
    lo = d["PBR"].min()
    print(f"\n  直近から、この12年のいちばん低い水準まで {1 - lo / d['PBR'].iloc[-1]:.0%} の下落")

    V.hr("2. 年ごとの底")
    g = d.copy()
    g["年"] = [m.year for m in g.index]
    t = g.groupby("年")["PBR"].agg(いちばん低い="min", 中央値="median", いちばん高い="max")
    print(t.round(2).to_string())

    V.hr("3. アプリの大人買いラインは、現実的な水準か")
    cur = float(d["PBR"].iloc[-1])
    lad = cfg["market"]["ladder"]
    snap_pbr = 1.86   # 日経平均PBR（2026-09 実測）
    print(f"  自分のデータの市場PBR {cur:.2f} ／ 日経平均PBR {snap_pbr:.2f}")
    print(f"  （構成銘柄も加重も違うので水準はずれる。比率で読む）\n")
    print(f"  {'段（日経PBR）':>14s}{'いまからの下落率':>16s}"
          f"{'この12年で到達したか':>22s}")
    for row in lad:
        target = float(row["pbr"])
        drop = 1 - target / snap_pbr
        # 同じ比率まで下がったことがあるか
        equiv = cur * (target / snap_pbr)
        hit = (d["PBR"] <= equiv).any()
        when = str(d[d["PBR"] <= equiv].index.min()) if hit else "—"
        print(f"  {target:>14.2f}{drop:>15.0%}{('✅ ' + when) if hit else '❌ 一度も無い':>24s}")
    print("\n  ❌ の段には、この12年で一度も到達していない。")
    print("  つまり**そこに置いた現金は12年間ずっと寝ていた**ことになる。")

    V.hr("4. PBRが低いときに買うと、本当に報われたか")
    # 市場PBRの5分位ごとに、その後3年の市場リターン（等ウェイト指数）
    panel = monthly_panel()
    close = panel["close"]
    paid = panel["paid"]
    prev = close.shift(1)
    ret = ((close + paid) / prev - 1)
    ok = ret.notna().sum(axis=1) >= 50
    r = ret[ok].apply(lambda s: s.dropna().clip(s.dropna().quantile(0.01),
                                                s.dropna().quantile(0.99)).mean(), axis=1)
    idx = (1 + r).cumprod()
    fwd = {}
    for h in (1, 3, 5):
        f = idx.shift(-h * 12) / idx - 1
        fwd[f"{h}年後"] = f.reindex(d.index)
    f = pd.DataFrame(fwd)
    f["PBR"] = d["PBR"]
    f = f.dropna(subset=["PBR"])
    q = pd.qcut(f["PBR"], 5, labels=["いちばん安い", "安い", "ふつう", "高い", "いちばん高い"])
    print(f.groupby(q, observed=True)[[c for c in f.columns if c.endswith("年後")]]
          .median().mul(100).round(1).to_string())
    print("\n  安いときに買うほど、その後のリターンが高いか。")
    print("  ※ 12年しか無いので、独立した観測は実質2〜3回ぶんしかない。読みすぎないこと。")


if __name__ == "__main__":
    main()
