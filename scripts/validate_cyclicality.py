"""検証13 — 「景気敏感かどうか」を、業種ではなく利益の振れ幅で測る。

【なぜやるか】
検証9で分かったこと。

    業種は **平時の減配** を予測しない（2014年と2020年の順位相関 0.08）
    しかし **不況時の配当の落ち込み** は予測する
        リーマン ↔ コロナ の順位相関 +0.622
        リーマン ↔ 震災   の順位相関 +0.455

同じ業種が毎回やられる。鉄鋼・非鉄金属・機械・電気機器・輸送用機器。
だが業種は33個しかなく、**同じ業種の中の差を捨てている**。同じ「機械」でも、
景気で利益が半分になる会社と、ほとんど動かない会社がある。

有報を10年ぶん取り込んだので、会社ごとに**利益の振れ幅**が直接測れるようになった。

    利益の振れ幅 ＝ 純利益（または営業CF）の変動係数
                   ＝ 標準偏差 ÷ 平均

これが業種より細かく、かつ理屈のうえでもまっとうな「景気敏感度」になるはず。
**業種上限のかわりに、これで上限をかけられるなら、そのほうが正確。**

【測り方】
FY2014〜FY2018 の5年で振れ幅を測り、**その先**（コロナを含む期間）の
配当の落ち込みと5年リターンを見る。振れ幅を測った期間と、答え合わせの期間は重ねない。

使い方:
    uv run python scripts/validate_cyclicality.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                           # noqa: E402
import pandas as pd                          # noqa: E402
from scipy.stats import spearmanr            # noqa: E402

from modules import valreport as V           # noqa: E402
from modules.dividend_history import annual_dps   # noqa: E402
from modules.hist_panel import load_cached   # noqa: E402
from modules.store import read_df            # noqa: E402

FIT = (2014, 2018)      # 振れ幅を測る期間
SHOCK = (2019, (2020, 2021))   # コロナ：2019年度を基準に、2020〜2021の最低


def cyclicality(p: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    """会社ごとの利益・営業CF・売上の振れ幅（変動係数）。"""
    w = p[p["fiscal_year"].between(lo, hi)]
    rows = []
    for t, g in w.groupby("ticker"):
        if len(g) < 4:
            continue
        rec = {"ticker": t, "年数": len(g)}
        for col, name in (("net_income", "純利益"), ("operating_cf", "営業CF"),
                          ("sales", "売上"), ("margin", "利益率")):
            v = g[col].dropna()
            if len(v) < 4 or v.mean() == 0:
                rec[name] = np.nan
                continue
            rec[name] = float(v.std() / abs(v.mean()))
        # 赤字を出した回数
        rec["赤字の年数"] = int((g["net_income"] < 0).sum())
        # いちばん悪い年の利益 ÷ いちばん良い年の利益
        ni = g["net_income"].dropna()
        rec["最悪÷最良"] = float(ni.min() / ni.max()) if len(ni) >= 4 and ni.max() > 0 else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


def dividend_drop(before: int, after: tuple[int, int]) -> pd.DataFrame:
    dv = read_df("SELECT ticker, date, amount FROM dividends")
    dv["date"] = pd.to_datetime(dv["date"])
    rows = []
    for t, g in dv.groupby("ticker"):
        tb = annual_dps(g[["date", "amount"]])
        if tb.empty or before not in tb.index:
            continue
        b = float(tb.loc[before, "dps"])
        if b <= 0:
            continue
        vals = [float(tb.loc[y, "dps"]) for y in range(after[0], after[1] + 1)
                if y in tb.index]
        if not vals:
            continue
        rows.append({"ticker": t, "配当の減り": 1 - min(min(vals) / b, 1.0)})
    return pd.DataFrame(rows)


def main() -> None:
    print("検証13 — 景気敏感度を、業種ではなく利益の振れ幅で測る")
    p = load_cached()
    cyc = cyclicality(p, *FIT)
    print(f"  FY{FIT[0]}〜{FIT[1]} の5年で振れ幅を測れた銘柄 {len(cyc):,}")

    drop = dividend_drop(*SHOCK)
    uni = read_df("SELECT ticker, sector33 FROM universe")
    d = cyc.merge(drop, on="ticker", how="inner").merge(uni, on="ticker", how="left")
    print(f"  そのうちコロナの落ち込みが測れた銘柄 {len(d):,}")

    V.hr("1. 振れ幅は、コロナの配当の落ち込みを当てたか")
    for name in ("純利益", "営業CF", "売上", "利益率", "最悪÷最良"):
        m = d[name].notna()
        if m.sum() < 200:
            continue
        r = spearmanr(d.loc[m, name], d.loc[m, "配当の減り"]).statistic
        mark = "✅" if abs(r) >= 0.10 else "  "
        print(f"  {mark} {name + 'の振れ幅':<16s} 相関 {r:>+6.3f}  n={int(m.sum()):,}")
    r = spearmanr(d["赤字の年数"], d["配当の減り"]).statistic
    print(f"     {'赤字の年数':<16s} 相関 {r:>+6.3f}")
    print("\n  「最悪÷最良」は小さいほど景気に振られる会社なので、相関は負になるはず。")

    V.hr("2. 振れ幅の5分位ごとの落ち込み")
    for name in ("純利益", "営業CF"):
        m = d[name].notna()
        if m.sum() < 500:
            continue
        q = pd.qcut(d.loc[m, name].rank(method="first"), 5,
                    labels=["振れが小さい", "やや小さい", "ふつう", "やや大きい", "振れが大きい"])
        g = d[m].groupby(q, observed=True).agg(
            n=("ticker", "count"), 配当の減り=("配当の減り", "mean"),
            減配した割合=("配当の減り", lambda s: (s > 0.001).mean()))
        print(f"\n  【{name}の振れ幅】")
        print(g.assign(配当の減り=lambda x: (x.配当の減り * 100).round(1),
                       減配した割合=lambda x: (x.減配した割合 * 100).round(1)).to_string())

    V.hr("3. 業種と、利益の振れ幅。どちらがよく当てるか")
    sec = d.groupby("sector33")["配当の減り"].transform("mean")
    m = d["純利益"].notna() & sec.notna()
    r_sec = spearmanr(sec[m], d.loc[m, "配当の減り"]).statistic
    r_cyc = spearmanr(d.loc[m, "純利益"], d.loc[m, "配当の減り"]).statistic
    print(f"  業種の平均落ち込み（※同じ期間の答えを使っているので有利）  相関 {r_sec:+.3f}")
    print(f"  利益の振れ幅（先の期間の情報は一切使っていない）          相関 {r_cyc:+.3f}")
    print("\n  業種のほうは**答えを見て作った値**なので、本来は勝って当たり前。")
    print("  それでも振れ幅が近い値を出すなら、振れ幅のほうが実用的（先に測れるので）。")

    V.hr("4. 振れ幅で足切りすると、平時のリターンは犠牲になるか")
    q = p[p["fiscal_year"].isin([2018, 2020]) & p["ret_5y"].notna()
          & (p["start_yield"] > 0)].merge(cyc[["ticker", "純利益"]], on="ticker", how="left")
    hi = q["start_yield"] >= q.groupby("fiscal_year")["start_yield"].transform(
        lambda s: s.quantile(0.70))
    thr = q["純利益"].quantile(0.70)
    print(f"  高利回り上位30%（n={int(hi.sum()):,}）に、振れ幅の条件を重ねる")
    V.gate_effect(q, hi, [
        ("  ＋ 利益の振れ幅が上位30%でない", q["純利益"] <= thr),
        ("  ＋ 赤字の年が無い", q.merge(cyc[["ticker", "赤字の年数"]], on="ticker",
                                   how="left")["赤字の年数"].fillna(9) == 0),
    ], {"リターン": "ret_5y", "DPS成長": "dps_growth_5y", "減配の発生": "cut_5y"})


if __name__ == "__main__":
    main()
