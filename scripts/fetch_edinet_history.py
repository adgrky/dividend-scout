"""過去の有価証券報告書を取り込んで、検証のコホートを増やす。

【なぜ要るか】
A層（増配余力）の検証は、いま FY2022 起点の1コホート・3年しかできていない。
コホートが1つでは「時期をまたいで安定しているか」が確かめられず、
足切りを既定にしてよいか判断できない。

【何ができるか】
有報の「主要な経営指標等の推移」には5年分入っている。つまり

    2018年提出の有報 → FY2014〜FY2018
    2022年提出の有報 → FY2018〜FY2022
    いま持っているもの → FY2021〜FY2026

の2回取れば **FY2014〜FY2026** が埋まり、2016・2018・2020年起点の
コホートが作れる（本検証と同じ起点）。

EDINET の保存期間は実測で **2017年まで**。
    2015年6月 → 0件 ／ 2016年6月 → 0件
    2017年6月 → 727件 ／ 2018年6月 → 1,490件 ／ 2019年6月 → 1,907件

【上書きしない】
古い有報は数字を遡及修正していることがある。新しい有報のほうが正確なので、
**すでに持っている年度は書き換えない**。埋まっていない年度だけ足す。

使い方:
    uv run python scripts/fetch_edinet_history.py --year 2018 --index
    uv run python scripts/fetch_edinet_history.py --year 2018 --fetch
    uv run python scripts/fetch_edinet_history.py --year 2018 --index --fetch
    uv run python scripts/fetch_edinet_history.py --year 2018 --fetch --limit 50
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd                                              # noqa: E402

from modules import edinet                                       # noqa: E402
from modules.store import connect, init_db, read_df, upsert_df   # noqa: E402

_DDL = """
CREATE TABLE IF NOT EXISTS edinet_doc_index (
    doc_id      TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    filer_name  TEXT,
    period_end  TEXT,
    submit_date TEXT,
    submit_year INTEGER,
    fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_index_year ON edinet_doc_index(submit_year);
"""
_COLS = ["doc_id", "code", "filer_name", "period_end", "submit_date",
         "submit_year", "fetched_at"]
_SUMMARY_COLS = ["ticker", "fiscal_year", "basis", "sales", "ordinary_income",
                 "net_income", "eps", "dps", "payout_ratio", "roe", "equity_ratio",
                 "net_assets", "total_assets", "operating_cf", "employees"]


def _log(m: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def build_index(year: int) -> int:
    """その年に提出された有報の一覧を作る。"""
    start, end = date(year, 1, 1), min(date(year, 12, 31), date.today())
    days = (end - start).days + 1
    _log(f"{year}年の提出一覧を作ります（{days} 日ぶん）…")
    rows, day = [], start
    for i in range(days):
        if i % 30 == 0:
            _log(f"  {i / days:5.1%} {day} まで（{len(rows)} 件）")
        try:
            rows.extend(edinet.list_documents(day))
        except Exception as exc:
            _log(f"  {day} で失敗（続行）: {exc}")
        day += timedelta(days=1)
        time.sleep(0.15)
    if not rows:
        _log(f"{year}年は0件でした（EDINET の保存期間の外かもしれません）")
        return 0
    df = pd.DataFrame(rows)
    df["submit_year"] = year
    df["fetched_at"] = None
    # 同じ会社が複数回出す場合は、期末が新しいものを残す
    df = df.sort_values("period_end").drop_duplicates("code", keep="last")
    with connect() as conn:
        conn.executescript(_DDL)
    upsert_df("edinet_doc_index", df, _COLS)
    _log(f"{year}年の一覧づくり完了: {len(df)} 社")
    return len(df)


def fetch(year: int, limit: int | None = None) -> None:
    with connect() as conn:
        conn.executescript(_DDL)
    idx = read_df("SELECT * FROM edinet_doc_index WHERE submit_year = ? "
                  "AND (fetched_at IS NULL OR fetched_at = '')", (year,))
    if idx.empty:
        _log(f"{year}年で取りに行くものがありません（--index を先に実行してください）")
        return
    uni = read_df("SELECT code, ticker FROM universe").set_index("code")["ticker"]
    idx = idx[idx["code"].isin(uni.index)]
    if limit:
        idx = idx.head(limit)
    _log(f"{year}年：{len(idx)} 社ぶんの有報を取りに行きます（1社あたり約2.4秒）")

    # すでに持っている（銘柄, 年度）は書き換えない。古い有報は遡及修正が入っている
    have = read_df("SELECT ticker, fiscal_year FROM edinet_summary")
    seen = set(zip(have["ticker"], have["fiscal_year"])) if not have.empty else set()
    _log(f"  すでに持っている財務データ: {len(seen):,} 件（これは書き換えません）")

    added, failed, t0 = 0, 0, time.time()
    for n, r in enumerate(idx.itertuples(index=False), 1):
        if n % 25 == 0:
            el = time.time() - t0
            eta = el / n * (len(idx) - n)
            _log(f"  {n / len(idx):5.1%} {n}/{len(idx)}  新規 {added:,} 件"
                 f"  残り約 {eta / 60:.0f} 分")
        ticker = uni.get(r.code)
        try:
            got = edinet.fetch_company(r.doc_id, r.period_end)
            s = got.get("summary")
            if s is not None and len(s):
                s = s.copy()
                s["ticker"] = ticker
                s = s[[c for c in _SUMMARY_COLS if c in s.columns]]
                fresh = s[~s.apply(lambda x: (ticker, x["fiscal_year"]) in seen, axis=1)]
                if len(fresh):
                    upsert_df("edinet_summary", fresh, _SUMMARY_COLS)
                    added += len(fresh)
                    seen.update((ticker, fy) for fy in fresh["fiscal_year"])
        except Exception as exc:
            failed += 1
            if failed <= 5:
                _log(f"  {r.code} で失敗（続行）: {str(exc)[:80]}")
        with connect() as conn:
            conn.execute("UPDATE edinet_doc_index SET fetched_at = ? WHERE doc_id = ?",
                         (datetime.now().strftime("%Y-%m-%d"), r.doc_id))
    _log(f"{year}年：完了 {(time.time() - t0) / 60:.0f}分 ／ "
         f"新しく埋まった財務データ {added:,} 件 ／ 失敗 {failed} 社")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True, help="提出年（2017以降）")
    ap.add_argument("--index", action="store_true", help="提出一覧を作る")
    ap.add_argument("--fetch", action="store_true", help="有報を取りに行く")
    ap.add_argument("--limit", type=int, default=None, help="先頭N社だけ（動作確認用）")
    a = ap.parse_args()
    init_db()
    if not (a.index or a.fetch):
        ap.error("--index か --fetch のどちらかを指定してください")
    if a.index:
        build_index(a.year)
    if a.fetch:
        fetch(a.year, a.limit)


if __name__ == "__main__":
    main()
