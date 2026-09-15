"""高配当トラップの検出。

高配当株で最も損をするのは「利回りが高いから買ったら減配した」パターン。
市場は往々にして減配を先に織り込んでおり、異常に高い利回りは割安のサインでは
なく警告であることが多い。ここは加点ではなく **減点** として働く。

各トラップは 0〜1 の強さを返し、合計を総合スコアから引く（config の
weights.trap_penalty で効き具合を調整する）。検出内容は必ず日本語のラベルで
残し、カルテで「なぜ減点されたか」を読めるようにする。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 減点の重み（合計でおよそ 0〜35 点ぶん引かれる想定）
_PENALTY = {
    "yield_trap": 12.0,        # 異常高利回り × 52週安値圏
    "accrual_gap": 6.0,        # 利益と営業CFの乖離
    "payout_creep": 7.0,       # 配当性向が急上昇
    "dilution": 4.0,           # 発行済株数の増加
    "earnings_slide": 6.0,     # 利益の連続減少
}

_LABELS = {
    "yield_trap": "利回りが異常に高く株価は52週安値圏（市場は減配を織り込んでいる可能性）",
    "accrual_gap": "純利益に営業CFが追いついていない（利益の質が低い）",
    "payout_creep": "配当性向が急上昇（利益で配当を賄えなくなりつつある）",
    "dilution": "発行済株数が増加（1株あたりの価値が薄まっている）",
    "earnings_slide": "利益が連続で減少（増配の原資が細っている）",
}


def detect(df: pd.DataFrame, config: dict) -> tuple[pd.Series, pd.Series]:
    """Returns (減点の合計, 検出されたトラップのラベル一覧)。"""
    t = config["traps"]
    flags = pd.DataFrame(index=df.index)

    # 1) 異常高利回り × 52週安値圏
    flags["yield_trap"] = (
        (df["dividend_yield"] > t["extreme_yield"])
        & (df["pos_52w"] < t["near_low_threshold"])
    ).fillna(False)

    # 2) 純利益と営業CFの乖離（アクルーアル）
    gap = (df["net_income"] - df["operating_cf"]) / df["net_income"].abs().replace(0, np.nan)
    flags["accrual_gap"] = (gap > t["accrual_gap_threshold"]).fillna(False)

    # 3) 配当性向の急上昇
    flags["payout_creep"] = (
        (df["payout_ratio"] - df["payout_ratio_prev"] > t["payout_creep_delta"])
        & (df["payout_ratio"] > t["payout_creep_floor"])
    ).fillna(False)

    # 4) 希薄化
    flags["dilution"] = (df["share_growth"] > t["dilution_threshold"]).fillna(False)

    # 5) 利益の連続減少
    flags["earnings_slide"] = (df["ni_declining_years"] >= t["earnings_slide_years"]).fillna(False)

    penalty = sum(flags[k].astype(float) * v for k, v in _PENALTY.items())
    labels = flags.apply(lambda r: [_LABELS[k] for k in _PENALTY if r.get(k)], axis=1)
    return penalty, labels
