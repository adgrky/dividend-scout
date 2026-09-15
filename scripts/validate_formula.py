"""検証8 — 買い付け優先度の式そのものは、単純な順位に勝つのか。

【なぜやるか】
層ごと（A〜F）の予測力は測ってきたが、**組み上げた式を端から端まで測ったことが
一度も無い**。config.yaml にはこう書いてある。

    優先度 = √(割安度 × 配当継続) × 補完度係数 − トラップ減点

「加重和にすると『安いが配当が危ない』が上位に来るので、両方そろって初めて買う、
という性質を式にする」——理屈は通っている。だが**理屈で作った式は、理屈で作った式**。
「ゲートを通った中で、単に利回りが高い順」に勝てなければ、この式は複雑なだけの飾りになる。

【比べるもの】
    ① 単に利回りが高い順            ← いちばん単純な対抗馬
    ② 自己利回りパーセンタイルが高い順
    ③ 加重和（割安度 × 0.5 ＋ 継続 × 0.5）
    ④ 相乗平均 √(割安度 × 継続)       ← アプリの式の骨
    ⑤ ④ − トラップ減点                ← アプリの式
    ⑥ ⑤ に減配歴ゼロのゲートを重ねたもの

各コホートで上位30銘柄を取り、5年後のトータルリターン・DPS成長・減配率を見る。
前半（2014・2016）で決めて、後半（2018・2020）で答え合わせする。

使い方:
    uv run python scripts/validate_formula.py
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
from modules.store import read_df            # noqa: E402

VAL_CSV = Path("data/val_v3.csv")


def _pct(s: pd.Series, by: pd.Series, ascending: bool = True) -> pd.Series:
    """コホートの中での順位（0〜100）。同じ日の銘柄どうしでだけ比べる。"""
    return s.groupby(by).rank(pct=True, ascending=ascending) * 100


def build(config: dict) -> pd.DataFrame:
    if not VAL_CSV.exists():
        raise SystemExit(
            "data/val_v3.csv がありません。先に次を実行してください:\n"
            "    .venv/bin/python scripts/validate_score.py --out data/val_v3.csv")
    d = pd.read_csv(VAL_CSV)
    d["cuts_10y"] = -d["cuts_10y_neg"]
    d["asof_dt"] = pd.to_datetime(d["asof"])

    # 有報から配当性向・営業CFの厚みを持ってくる（アプリの「配当継続」に相当）
    p = load_cached()[["ticker", "fiscal_year", "fy_end", "payout", "ocf_cover"]]
    p = p.dropna(subset=["fy_end"])
    p["known"] = p["fy_end"] + pd.DateOffset(months=3)
    rows = []
    for asof, g in d.groupby("asof_dt"):
        sub = p[p["known"] <= asof].sort_values("known").groupby("ticker").tail(1)
        m = g.merge(sub[["ticker", "payout", "ocf_cover"]], on="ticker", how="left")
        rows.append(m)
    d = pd.concat(rows, ignore_index=True)

    bp = config["buy_priority"]
    w_self, w_abs = float(bp["self_yield_weight"]), float(bp["abs_yield_weight"])
    swing = float(bp["fit_swing"])

    # 割安度（アプリと同じ合成）
    self_rank = _pct(d["yield_percentile"], d["asof"], ascending=False)
    abs_rank = _pct(d["dividend_yield"], d["asof"])
    d["割安度"] = (w_self * self_rank + w_abs * abs_rank.fillna(50)).clip(0, 100)

    # 配当継続（アプリの health に相当するものを、測れる材料だけで組む）
    parts = [
        _pct(d["cuts_10y"], d["asof"], ascending=False),      # 減配が少ない
        _pct(d["streak_no_cut"], d["asof"]),                  # 減配なしで続いた年数
        _pct(d["payout"].fillna(d["payout"].median()), d["asof"], ascending=False),
        _pct(d["ocf_cover"].fillna(d["ocf_cover"].median()), d["asof"]),
    ]
    d["配当継続"] = pd.concat(parts, axis=1).mean(axis=1).clip(0, 100)

    # トラップ減点（検証2で学習・検証の両方で効いた3つだけ）
    yr = d.groupby("asof")["dividend_yield"].rank(pct=True)
    pen = (yr >= 0.90).astype(float) * 12
    pen += ((d["payout"] > 0.70).fillna(False)).astype(float) * 7
    d["減点"] = pen

    # 補完度はポートフォリオの中身に依存するので、過去には再現できない。
    # 効き幅は ±10% と小さいので、ここでは中立（1.0）として扱う。
    d["補完度係数"] = 1.0 - swing / 2 + swing * 0.5

    d["① 利回り順"] = d["dividend_yield"]
    d["② 自己利回り順"] = -d["yield_percentile"]
    d["③ 加重和"] = 0.5 * d["割安度"] + 0.5 * d["配当継続"]
    d["④ 相乗平均"] = np.sqrt(d["割安度"].clip(lower=0) * d["配当継続"].clip(lower=0))
    d["⑤ 相乗平均−減点（アプリの式）"] = (d["④ 相乗平均"] * d["補完度係数"] - d["減点"]).clip(lower=0)
    d["⑥ ⑤＋減配歴ゼロ"] = d["⑤ 相乗平均−減点（アプリの式）"].where(d["cuts_10y"] == 0, -1)
    return d


METHODS = ["① 利回り順", "② 自己利回り順", "③ 加重和", "④ 相乗平均",
           "⑤ 相乗平均−減点（アプリの式）", "⑥ ⑤＋減配歴ゼロ"]


def report(d: pd.DataFrame, label: str, top_n: int = 30) -> pd.DataFrame:
    rows = [{"やり方": "（全銘柄を等分で持つ）", "銘柄数": len(d),
             "リターン": d["fwd_total_return"].median(),
             "DPS成長": d["fwd_dps_growth"].median(),
             "減配率": d["fwd_had_cut"].mean()}]
    for m in METHODS:
        sub = pd.concat([g.nlargest(top_n, m) for _, g in d.groupby("asof")])
        rows.append({"やり方": m, "銘柄数": len(sub),
                     "リターン": sub["fwd_total_return"].median(),
                     "DPS成長": sub["fwd_dps_growth"].median(),
                     "減配率": sub["fwd_had_cut"].mean()})
    t = pd.DataFrame(rows).set_index("やり方")
    print(f"\n  【{label}】各コホートで上位{top_n}銘柄")
    for n, r in t.iterrows():
        print(f"    {n:<30s} n={int(r['銘柄数']):>4,}  リターン {r['リターン']:>+7.1%}"
              f"  DPS成長 {r['DPS成長']:>+7.1%}  減配率 {r['減配率']:>5.1%}")
    return t


def main() -> None:
    cfg = load_config()
    d = build(cfg)
    print("検証8 — 買い付け優先度の式は、単純な順位に勝つのか")
    print(f"  延べ {len(d):,} 銘柄 / コホート {sorted(d['asof'].unique())}")
    print(f"  配当性向が取れた割合 {d['payout'].notna().mean():.0%}")

    V.hr("1. 全コホート")
    report(d, "全コホート")

    V.hr("2. 前半で決めて、後半で答え合わせ")
    ks = sorted(d["asof"].unique())
    h = len(ks) // 2
    tr = report(d[d["asof"].isin(ks[:h])], f"学習 {ks[:h]}")
    te = report(d[d["asof"].isin(ks[h:])], f"検証 {ks[h:]}")

    V.hr("3. 学習でいちばん良かった式は、検証でも良かったか")
    best_tr = tr.drop(index="（全銘柄を等分で持つ）")["リターン"].idxmax()
    best_te = te.drop(index="（全銘柄を等分で持つ）")["リターン"].idxmax()
    print(f"  学習でリターン最良: {best_tr}")
    print(f"  検証でリターン最良: {best_te}")
    app = "⑤ 相乗平均−減点（アプリの式）"
    simple = "① 利回り順"
    for nm, t in (("学習", tr), ("検証", te)):
        da = t.loc[app, "リターン"] - t.loc[simple, "リターン"]
        dc = t.loc[app, "減配率"] - t.loc[simple, "減配率"]
        print(f"  {nm}：アプリの式 − 利回り順  リターン {da:>+6.1%}  減配率 {dc:>+6.1f}pt")
    print("\n  **アプリの式が、学習でも検証でも利回り順に勝てないなら、"
          "\n    複雑にしているだけということになる。**")

    V.hr("4. 上位何銘柄まで取るか")
    for n in (10, 20, 30, 50, 100):
        sub = pd.concat([g.nlargest(n, app) for _, g in d.groupby("asof")])
        s2 = pd.concat([g.nlargest(n, simple) for _, g in d.groupby("asof")])
        print(f"  上位{n:>3d}銘柄  アプリの式 {sub['fwd_total_return'].median():>+7.1%}"
              f"（減配 {sub['fwd_had_cut'].mean():>5.1%}）"
              f"   利回り順 {s2['fwd_total_return'].median():>+7.1%}"
              f"（減配 {s2['fwd_had_cut'].mean():>5.1%}）")


if __name__ == "__main__":
    main()
