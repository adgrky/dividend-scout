"""NISA枠の使い方を計算する。

【制度（2026-09-16 に裏取り）】
    成長投資枠  年 240万円 ／ 生涯 1,200万円（NISA全体 1,800万円のうち）
    売却すると **翌年1月1日** に **簿価（取得価格）ぶん** の生涯枠が復活する
    年間投資枠（240万円）は復活しない
    出典 https://www.soico.jp/no1/news/securities/5078
         https://www.soico.jp/no1/news/securities/8996

【考え方 — 検証15】
売らずに持ち続ける前提なら、NISAの値打ちは **配当への課税 20.315% が消えること**
にほぼ尽きる。譲渡益の非課税は、売らないかぎり実現しないから。だから

    枠 1円あたりの毎年の節税 ＝ その銘柄の現在利回り × 20.315%

**枠には利回りが高い銘柄を入れる。** それだけ。

ややこしいのは「すでに特定口座で持っているものを移すか」のほう。移すには一度売るので
含み益に税がかかる。

    移すコスト（1回だけ）  ＝ 含み益 × 20.315%
    移す便益（毎年）       ＝ 年間配当 × 20.315%
    回収にかかる年数       ＝ 含み益 ÷ 年間配当

含み損の銘柄は逆で、売れば損失が確定して同じ年の利益と通算できる。ただし
**同じ年に他で利益を確定している場合だけ**なので、既定では数えない。

そしていちばん大事な点。**移し替えと新規買いは、同じ年240万円の枠を奪い合う。**
新規買いのほうが優先（含み益への課税が無いのでコストがゼロ）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TAX = 0.20315
GROWTH_LIFETIME = 12_000_000
GROWTH_ANNUAL = 2_400_000


def room(positions: pd.DataFrame) -> dict:
    """成長投資枠の残り。枠は **簿価（取得額）** で数える。"""
    if positions is None or positions.empty:
        return {"使った枠": 0.0, "残りの枠": float(GROWTH_LIFETIME),
                "年間の枠": float(GROWTH_ANNUAL), "最短何年": 5.0,
                "特定の税": 0.0, "特定の年間配当": 0.0}
    nisa = positions[positions["account"] == "nisa"]
    spec = positions[positions["account"] != "nisa"]
    used = float(nisa["cost_value"].sum())
    left = max(0.0, GROWTH_LIFETIME - used)
    spec_div = float(spec["annual_dividend"].sum())
    return {
        "使った枠": used, "残りの枠": left, "年間の枠": float(GROWTH_ANNUAL),
        "最短何年": float(np.ceil(left / GROWTH_ANNUAL)) if left > 0 else 0.0,
        "特定の税": spec_div * TAX, "特定の年間配当": spec_div,
        "NISAの年間配当": float(nisa["annual_dividend"].sum()),
        "NISAの利回り": (float(nisa["annual_dividend"].sum())
                     / float(nisa["eval_value"].sum())) if len(nisa) else 0.0,
        "特定の利回り": (spec_div / float(spec["eval_value"].sum())) if len(spec) else 0.0,
    }


def move_candidates(positions: pd.DataFrame, horizon: int = 20,
                    count_loss_offset: bool = False) -> pd.DataFrame:
    """特定口座の銘柄を、NISAに移す価値が高い順に並べる。

    Parameters
    ----------
    horizon : int
        何年持ち続ける前提か。回収年数がこれより短ければ得になる。
    count_loss_offset : bool
        含み損の銘柄で、損益通算による税の戻りを数えるか。
        同じ年に他で利益を確定していないかぎり戻らないので、既定は数えない。
    """
    if positions is None or positions.empty:
        return pd.DataFrame()
    s = positions[positions["account"] != "nisa"].copy()
    s = s[s["annual_dividend"] > 0]
    if s.empty:
        return pd.DataFrame()
    s["含み益"] = s["eval_value"] - s["cost_value"]
    s["移すときの税"] = s["含み益"].clip(lower=0) * TAX
    s["損出しの戻り"] = ((-s["含み益"]).clip(lower=0) * TAX) if count_loss_offset else 0.0
    s["毎年の節税"] = s["annual_dividend"] * TAX
    s["回収年数"] = np.where(
        s["毎年の節税"] > 0,
        (s["移すときの税"] - s["損出しの戻り"]) / s["毎年の節税"], np.inf)
    s[f"{horizon}年の得"] = (s["毎年の節税"] * horizon - s["移すときの税"]
                          + s["損出しの戻り"])
    s["使う枠"] = s["eval_value"]
    s["枠あたりの得"] = s[f"{horizon}年の得"] / s["使う枠"].replace(0, np.nan)
    s["現在利回り"] = s["annual_dividend"] / s["eval_value"].replace(0, np.nan)
    return s.sort_values("枠あたりの得", ascending=False)


def plan(positions: pd.DataFrame, monthly_deposit: float, horizon: int = 20,
         count_loss_offset: bool = False) -> dict:
    """この1年で、枠をどう使うのが良いかの段取り。

    新規買いを先に置き、**余った年間枠**で移し替える。
    """
    r = room(positions)
    yearly = monthly_deposit * 12
    for_new = min(yearly, r["年間の枠"], r["残りの枠"])
    for_move = max(0.0, min(r["年間の枠"] - for_new, r["残りの枠"] - for_new))

    cand = move_candidates(positions, horizon, count_loss_offset)
    # 株は一部だけ売ることもできるので、枠に入り切らない銘柄は**途中まで**移す。
    # 枠あたりの得が大きい順に詰めれば、それが最善になる（分割できる荷物の詰め方）。
    picked, left, gain = [], for_move, 0.0
    if not cand.empty:
        for _, row in cand.iterrows():
            if row[f"{horizon}年の得"] <= 0 or left <= 0:
                continue
            take = min(float(row["使う枠"]), left)
            if take < 1000:                 # 細切れは作らない
                continue
            frac = take / float(row["使う枠"])
            r2 = row.copy()
            r2["使う枠"] = take
            r2["移す株数"] = float(row["shares"]) * frac
            r2["一部だけ"] = frac < 0.999
            for c in ("含み益", "移すときの税", "毎年の節税", f"{horizon}年の得",
                      "annual_dividend", "eval_value", "cost_value"):
                if c in r2.index:
                    r2[c] = float(row[c]) * frac
            picked.append(r2)
            left -= take
            gain += float(r2[f"{horizon}年の得"])
    moves = pd.DataFrame(picked) if picked else pd.DataFrame()
    return {**r, "今年の新規買いに使う枠": for_new, "今年の移し替えに使える枠": for_move,
            "移す銘柄": moves, "移して得られる額": gain, "使い切れない枠": left,
            "全部移した場合の得": (cand[cand[f"{horizon}年の得"] > 0][f"{horizon}年の得"].sum()
                          if not cand.empty else 0.0),
            "horizon": horizon}


def misplaced(positions: pd.DataFrame) -> dict:
    """利回りの低い銘柄がNISAに、高い銘柄が特定に入っていないか。"""
    if positions is None or positions.empty:
        return {}
    p = positions.copy()
    p["現在利回り"] = p["annual_dividend"] / p["eval_value"].replace(0, np.nan)
    cut = float(p["現在利回り"].median())
    lo_nisa = p[(p["account"] == "nisa") & (p["現在利回り"] <= cut)]
    hi_spec = p[(p["account"] != "nisa") & (p["現在利回り"] > cut)]
    return {"境目の利回り": cut,
            "NISAにある低利回り": lo_nisa[["code", "name_jpx", "eval_value",
                                     "annual_dividend", "現在利回り"]],
            "特定にある高利回り": hi_spec[["code", "name_jpx", "eval_value",
                                    "annual_dividend", "現在利回り"]]}
