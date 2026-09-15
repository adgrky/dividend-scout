"""高配当トラップの検出。

高配当株で最も損をするのは「利回りが高いから買ったら減配した」パターン。
市場は往々にして減配を先に織り込んでおり、異常に高い利回りは割安のサインでは
なく警告であることが多い。ここは加点ではなく **減点** として働く。

【2026-09-16 の検証で入れ替えた】
scripts/validate_cut_signals.py で、延べ15,894 銘柄・年について「その兆候が
出た会社の何割が2年以内に減配したか」を測った（全体の減配率 18.8%）。

    兆候                          発火     減配率   リフト  再現率
    利回りが上位10%               1,852   36.7%   1.95   22.7%   ★
    利回り上位10% かつ 52週安値圏     595   42.0%   2.23    8.3%   ★
    配当性向が1年で20pt上昇         1,123   27.3%   1.45   10.3%   ★
    52週安値圏だけ               3,326   23.7%   1.26   26.3%
    利益に現金が伴っていない          2,001   21.6%   1.15   14.4%
    発行済株数が3%以上増えた         1,060   17.7%   0.94    6.3%   ←効かない

**利回りは絶対値ではなく、その時点の市場の中での順位で見るべきだった。**
以前の「6.5%超」という絶対基準は、延べ15,884件のうち18件しか切らなかった
（＝ほぼ発火していなかった）。一方で上位10%という相対基準は、高利回り候補の
減配率を 38.4% → 33.0% に下げる（5.3pt）。

希薄化（発行済株数の増加）はリフト0.94で、**減配とは逆向き**だった。自社株
発行が増配の妨げになるという理屈は、日本株の実データでは支持されない。減点から外す。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 減点の重み。検証で測ったリフトの順に並べる。
_PENALTY = {
    "yield_extreme": 12.0,     # 利回りが市場の上位10%（リフト 1.95）
    "yield_trap": 8.0,         # さらに52週安値圏（リフト 2.23。上と重ねて20点）
    "payout_creep": 7.0,       # 配当性向が急上昇（リフト 1.47）
    "earnings_slide": 3.0,     # 利益の連続減少（リフト 1.2 前後。弱い）
    "accrual_gap": 2.0,        # 利益と営業CFの乖離（リフト 1.15。ほぼ効かない）
}

_LABELS = {
    "yield_extreme": "利回りが市場の上位10%に入っている（この水準の銘柄は2年以内の減配率が1.95倍）",
    "yield_trap": "そのうえ株価が52週安値圏（市場は減配を織り込んでいる可能性。減配率2.23倍）",
    "payout_creep": "配当性向が急上昇（利益で配当を賄えなくなりつつある。減配率1.47倍）",
    "earnings_slide": "利益が連続で減少（増配の原資が細っている）",
    "accrual_gap": "純利益に営業CFが追いついていない（利益の質が低い）",
}


def detect(df: pd.DataFrame, config: dict) -> tuple[pd.Series, pd.Series]:
    """Returns (減点の合計, 検出されたトラップのラベル一覧)。"""
    t = config["traps"]
    flags = pd.DataFrame(index=df.index)

    # 1) 利回りが市場の上位何%か。無配・欠損を混ぜると順位が歪むので、
    #    配当を出している銘柄のあいだだけで順位をつける。
    y = pd.to_numeric(df.get("dividend_yield"), errors="coerce")
    payers = y > 0
    rank = pd.Series(np.nan, index=df.index)
    if payers.sum() >= 50:
        rank[payers] = y[payers].rank(pct=True)
    extreme = (rank >= t["extreme_yield_percentile"]).fillna(False)
    # 相対順位が取れない（候補が少ない）ときのための絶対値の保険
    extreme |= (y > t["extreme_yield"]).fillna(False)
    flags["yield_extreme"] = extreme
    flags["yield_trap"] = (extreme & (df["pos_52w"] < t["near_low_threshold"])).fillna(False)

    # 2) 配当性向の急上昇。水準が低いうちの上昇は余力の範囲なので除く
    #    （実測: 水準50%超なら リフト1.47、50%以下なら 1.35 で発火数も少ない）
    flags["payout_creep"] = (
        (df["payout_ratio"] - df["payout_ratio_prev"] > t["payout_creep_delta"])
        & (df["payout_ratio"] > t["payout_creep_floor"])
    ).fillna(False)

    # 3) 利益の連続減少
    flags["earnings_slide"] = (df["ni_declining_years"] >= t["earnings_slide_years"]).fillna(False)

    # 4) 純利益と営業CFの乖離（アクルーアル）
    gap = (df["net_income"] - df["operating_cf"]) / df["net_income"].abs().replace(0, np.nan)
    flags["accrual_gap"] = (gap > t["accrual_gap_threshold"]).fillna(False)

    penalty = sum(flags[k].astype(float) * v for k, v in _PENALTY.items())
    labels = flags.apply(lambda r: [_LABELS[k] for k in _PENALTY if r.get(k)], axis=1)
    return penalty, labels
