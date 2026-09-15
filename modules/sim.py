"""毎月の積み立てを、実際の株価と配当で回すための土台。

検証14（買い増し vs 新規）と検証16（配当再投資）で共通に使う。
月末の終値・その時点の実績配当・分割調整は、すべて DB の値をそのまま使う。

**先読みを起こさないための約束**
    ・ある月に使ってよいのは、その月末までの株価と、その日までに権利落ちした配当だけ
    ・利回りは「直近12ヶ月に実際に払われた配当 ÷ その月末の終値」
    ・減配歴も、その月までの配当明細だけで数える
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.quality import trim_frame
from modules.store import read_df


def monthly_panel(min_month: str = "2004-01") -> dict:
    """月末終値・直近12ヶ月の配当・減配回数を、月 × 銘柄の表で返す。"""
    px, _ = trim_frame(read_df("SELECT ticker, date, close FROM prices"))
    px["date"] = pd.to_datetime(px["date"])
    px = px[px["close"] > 0]
    px["m"] = px["date"].dt.to_period("M")
    close = (px.sort_values("date").groupby(["m", "ticker"])["close"].last()
             .unstack().sort_index())

    dv = read_df("SELECT ticker, date, amount FROM dividends")
    dv["date"] = pd.to_datetime(dv["date"])
    dv["m"] = dv["date"].dt.to_period("M")
    paid = (dv.groupby(["m", "ticker"])["amount"].sum().unstack()
            .reindex(index=close.index, columns=close.columns).fillna(0.0))
    ttm = paid.rolling(12, min_periods=12).sum()

    # その月までに何回減配したか（年度をまたぐ比較は使わず、直近12ヶ月と
    # その1年前の12ヶ月を比べる素朴な形。毎月使うので軽さを優先する）
    prev = ttm.shift(12)
    cut_flag = ((ttm < prev * 0.999) & (prev > 0)).astype(float)
    cuts = cut_flag.rolling(120, min_periods=1).sum()

    keep = close.index >= pd.Period(min_month, "M")
    return {"close": close[keep], "ttm": ttm[keep], "paid": paid[keep],
            "cuts": cuts[keep]}


def yields(panel: dict) -> pd.DataFrame:
    with np.errstate(divide="ignore", invalid="ignore"):
        y = panel["ttm"] / panel["close"]
    return y.where(np.isfinite(y) & (y > 0) & (y < 0.30))
