"""表示用の書式。数字の読みやすさは判断の速さに直結する。"""
from __future__ import annotations

import math

import pandas as pd


def yen(v, digits: int = 0) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"¥{v:,.{digits}f}"


def yen_short(v) -> str:
    """億・万で丸めた金額。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    v = float(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e12:
        return f"{sign}{a / 1e12:.2f}兆円"
    if a >= 1e8:
        return f"{sign}{a / 1e8:,.0f}億円"
    if a >= 1e4:
        return f"{sign}{a / 1e4:,.0f}万円"
    return f"{sign}¥{a:,.0f}"


def pct(v, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:.{digits}f}%"


def num(v, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:,.{digits}f}"


def after_tax(amount: float, account: str, config: dict) -> float:
    """税引後の受取額。NISA は非課税、特定口座は 20.315%。"""
    rate = (config["portfolio"]["tax_rate_nisa"] if account == "nisa"
            else config["portfolio"]["tax_rate_specific"])
    return float(amount) * (1 - rate)


def csv_bytes(df: pd.DataFrame) -> bytes:
    """Excel で開いても文字化けしない CSV。"""
    return df.to_csv(index=False).encode("utf-8-sig")
