"""相場の水準を測って、「いくら入れて、いくら残すか」を決める材料を出す。

【なぜ必要か】
ヘムの手法は3本柱で、3本目が「暴落時の買い向かい」。
現金を総資産の約30%保持し、日経平均PBR0.8を底値目安に10分割で投入する。
入金額を毎回使い切る作りだと、この3本目がまるごと抜け落ちる。

【日経平均PBRの取り方】
日本経済新聞社が公開している日次の投資指標から取る（当月ぶんがHTMLで返る）。
過去分はページ上では JavaScript で読み込まれるため取れないので、
**取得するたびに DB へ貯めて自前の履歴を作る**。
長期の相場水準は、yfinance で取れる日経平均株価そのもののパーセンタイルで補う。

【大人買いラインの出し方】
PBR は「株価 ÷ 1株純資産」なので、1株純資産が変わらないとすれば
    目標PBR に対応する日経平均 = いまの日経平均 × 目標PBR ÷ いまのPBR
で逆算できる。これなら履歴が無くても、いくらまで下がったらどの水準か が出せる。
"""
from __future__ import annotations

import re
import urllib.request
from datetime import datetime

import pandas as pd

_PBR_URL = "https://indexes.nikkei.co.jp/nkave/archives/data?list=pbr"
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 Chrome/120 Safari/537.36")}
_NIKKEI_TICKER = "^N225"


def fetch_nikkei_pbr(timeout: int = 30) -> pd.DataFrame:
    """日経平均の日次PBR（当月ぶん）。columns: date, pbr_weighted, pbr_index。"""
    try:
        req = urllib.request.Request(_PBR_URL, headers=_UA)
        html = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception as exc:
        print(f"⚠️  日経平均PBRの取得に失敗: {exc}")
        return pd.DataFrame(columns=["date", "pbr_weighted", "pbr_index"])

    # テーブルのタグを区切りに潰してから、日付＋2つの数値を拾う。
    # タグと空白をまとめて1本の区切りにしないと「日付| |1.92」のように
    # 区切りが二重になって拾えない。
    txt = re.sub(r"<[^>]+>", "|", html)
    txt = re.sub(r"[\s|]+", "|", txt)
    rows = re.findall(r"(\d{4}\.\d{2}\.\d{2})\|([\d.]+)\|([\d.]+)", txt)
    if not rows:
        return pd.DataFrame(columns=["date", "pbr_weighted", "pbr_index"])
    df = pd.DataFrame(rows, columns=["date", "pbr_weighted", "pbr_index"])
    df["date"] = df["date"].str.replace(".", "-", regex=False)
    for c in ("pbr_weighted", "pbr_index"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # 先頭に「更新日付」が混ざることがあるので、PBRとして妥当な範囲だけ残す
    df = df[(df["pbr_weighted"] > 0.3) & (df["pbr_weighted"] < 10)]
    return df.dropna().drop_duplicates("date").reset_index(drop=True)


def fetch_nikkei_price(period: str = "max") -> pd.Series:
    """日経平均株価の終値（週足）。長期の水準を測るのに使う。"""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    try:
        df = yf.download(_NIKKEI_TICKER, period=period, interval="1wk",
                         progress=False, auto_adjust=False)
    except Exception as exc:
        print(f"⚠️  日経平均株価の取得に失敗: {exc}")
        return pd.Series(dtype=float)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close.dropna()


def snapshot(price: pd.Series, pbr: pd.DataFrame) -> dict:
    """いまの相場の水準をまとめる。"""
    out: dict = {}
    if not price.empty:
        now = float(price.iloc[-1])
        out["nikkei"] = now
        out["asof_price"] = price.index[-1].strftime("%Y-%m-%d")
        for years, key in ((10, "pct_10y"), (20, "pct_20y")):
            hist = price[price.index >= price.index.max() - pd.DateOffset(years=years)]
            if len(hist) > 52:
                out[key] = float((hist < now).mean())
        ma = price.rolling(40).mean()      # 週足40本 ≒ 200日移動平均
        if pd.notna(ma.iloc[-1]) and ma.iloc[-1] > 0:
            out["vs_ma200"] = now / float(ma.iloc[-1]) - 1
        hi = price[price.index >= price.index.max() - pd.DateOffset(years=3)].max()
        if hi:
            out["drawdown_3y"] = now / float(hi) - 1
    if pbr is not None and not pbr.empty:
        out["pbr"] = float(pbr["pbr_weighted"].iloc[-1])
        out["asof_pbr"] = str(pbr["date"].iloc[-1])
    return out


def buy_ladder(snap: dict, config: dict, total_assets: float) -> pd.DataFrame:
    """大人買いライン。

    「日経平均PBRがこの水準まで下がったら、取ってある現金のうち何%を入れる」
    という段階表を、日経平均の実際の数字に直して出す。
    """
    levels = config["market"]["ladder"]
    pbr_now = snap.get("pbr")
    nikkei_now = snap.get("nikkei")
    rows = []
    for step in levels:
        target_pbr = float(step["pbr"])
        share = float(step["deploy"])
        level = (nikkei_now * target_pbr / pbr_now) if (pbr_now and nikkei_now) else None
        rows.append({
            "日経平均PBR": target_pbr,
            "日経平均の水準": level,
            "いまからの下落率": (level / nikkei_now - 1) if (level and nikkei_now) else None,
            "投入する割合": share,
            "投入額": total_assets * share if total_assets else None,
            "到達済み": bool(pbr_now and pbr_now <= target_pbr),
        })
    return pd.DataFrame(rows)


def regime(snap: dict, config: dict) -> tuple[str, float, str]:
    """いまは買い向かう局面か、待つ局面か。

    Returns
    -------
    (区分, 平常時に投入してよい割合, 説明)
    """
    m = config["market"]
    pbr = snap.get("pbr")
    pct = snap.get("pct_10y")
    if pbr is None:
        return ("不明", 1.0, "日経平均PBRが取れていないため、判定できません")

    for band in m["regimes"]:
        if pbr <= float(band["pbr_below"]):
            note = band["note"]
            if pct is not None:
                note += f"（日経平均は過去10年の分布で上位 {1 - pct:.0%} の水準）"
            return (band["name"], float(band["deploy"]), note)
    last = m["regimes"][-1]
    return (last["name"], float(last["deploy"]), last["note"])


def record_snapshot(snap: dict, pbr: pd.DataFrame) -> int:
    """その日の相場をDBに残す。日経のサイトは当月ぶんしか返さないので、
    取るたびに貯めて自前の履歴を作る。"""
    from modules.store import upsert_df
    if not snap:
        return 0
    rows = []
    if pbr is not None and not pbr.empty:
        for r in pbr.itertuples(index=False):
            rows.append({"date": r.date, "pbr_weighted": r.pbr_weighted,
                         "pbr_index": r.pbr_index})
    today = snap.get("asof_pbr") or datetime.now().strftime("%Y-%m-%d")
    base = {"date": today, "nikkei": snap.get("nikkei"),
            "pct_10y": snap.get("pct_10y"), "vs_ma200": snap.get("vs_ma200")}
    merged = {r["date"]: r for r in rows}
    merged.setdefault(today, {"date": today})
    merged[today].update({k: v for k, v in base.items() if v is not None})
    df = pd.DataFrame(merged.values())
    return upsert_df("market_history", df,
                     ["date", "nikkei", "pbr_weighted", "pbr_index", "pct_10y", "vs_ma200"])


def load_snapshot() -> dict:
    """DBに残っている直近の相場。ネットに出ずに画面を描くため。"""
    from modules.store import read_df
    df = read_df("SELECT * FROM market_history ORDER BY date DESC LIMIT 1")
    if df.empty:
        return {}
    r = df.iloc[0]
    out = {"asof_pbr": r["date"]}
    for k in ("nikkei", "pbr_weighted", "pct_10y", "vs_ma200"):
        if pd.notna(r[k]):
            out["pbr" if k == "pbr_weighted" else k] = float(r[k])
    return out
