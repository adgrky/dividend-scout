"""EDINET から有価証券報告書を取って、5年分の指標と事業内容を DB に入れる。

2段階:
  index : 提出一覧を遡って「証券コード → 有報のdocID」を作る（1日1リクエスト）
  fetch : 対象銘柄の XBRL を落として解析する（1社1リクエスト・約300KB）

途中で止めても、次回は未取得ぶんだけ再開する。

使い方:
    uv run python scripts/fetch_edinet.py --index            # 一覧づくり（初回・約15分）
    uv run python scripts/fetch_edinet.py --fetch            # 対象銘柄の取得
    uv run python scripts/fetch_edinet.py --fetch --limit 50 # 動作確認
    uv run python scripts/fetch_edinet.py --index --fetch    # 通し
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import edinet                                        # noqa: E402
from modules.config import load_config                            # noqa: E402
from modules.pipeline import load_base, prescreen                 # noqa: E402
from modules.store import connect, init_db, read_df, upsert_df    # noqa: E402

_INDEX_COLS = ["code", "doc_id", "filer_name", "period_end", "submit_date", "fetched_at"]
_SUMMARY_COLS = ["ticker", "fiscal_year", "sales", "ordinary_income", "net_income", "eps",
                 "dps", "payout_ratio", "roe", "equity_ratio", "net_assets", "total_assets",
                 "operating_cf", "employees"]
_PROFILE_COLS = ["ticker", "business_ja", "business_en", "employees", "ex_dividend_date",
                 "dividend_rate", "industry_en", "dividend_policy", "policy_flags",
                 "policy_score", "updated_at"]


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def build_index(days: int) -> pd.DataFrame:
    _log(f"提出一覧を {days} 日ぶん遡ります（1日1リクエスト）…")
    df = edinet.build_index(days_back=days, progress=lambda p, m: _log(f"  {p:5.1%} {m}"))
    if df.empty:
        _log("提出一覧が空でした")
        return df
    df["fetched_at"] = None
    # すでに取得済みの fetched_at は消さない
    old = read_df("SELECT code, fetched_at FROM edinet_index").set_index("code")["fetched_at"]
    df["fetched_at"] = df["code"].map(old)
    upsert_df("edinet_index", df, _INDEX_COLS)
    _log(f"一覧づくり完了: {len(df)} 社")
    return df


def fetch_documents(limit: int | None, only_targets: bool) -> None:
    idx = read_df("SELECT * FROM edinet_index WHERE fetched_at IS NULL")
    if idx.empty:
        _log("未取得の有報はありません")
        return

    if only_targets:
        config = load_config()
        targets = {t[:-2] for t in prescreen(load_base(), config)}   # 9432.T -> 9432
        idx = idx[idx["code"].isin(targets)]
        _log(f"対象を候補銘柄に絞りました: {len(idx)} 社")

    if limit:
        idx = idx.head(limit)

    _log(f"{len(idx)} 社の有報を取得します（1社あたり約300KB）…")
    n_ok = n_ng = 0
    t0 = time.time()
    for i, r in enumerate(idx.itertuples(index=False), start=1):
        ticker = f"{r.code}.T"
        try:
            got = edinet.fetch_company(r.doc_id, r.period_end)
            summary, business = got["summary"], got["business"]
        except Exception as exc:
            _log(f"  取得失敗 {ticker}: {exc}")
            n_ng += 1
            continue

        if summary.empty:
            n_ng += 1
        else:
            summary.insert(0, "ticker", ticker)
            upsert_df("edinet_summary", summary, _SUMMARY_COLS)
            employees = summary["employees"].dropna()
            upsert_df("company_profile", pd.DataFrame([{
                "ticker": ticker,
                "business_ja": business,
                "employees": float(employees.iloc[-1]) if len(employees) else None,
                "dividend_policy": got["policy"],
                "policy_flags": json.dumps(got["policy_flags"], ensure_ascii=False),
                "policy_score": got["policy_score"],
                "updated_at": datetime.now().strftime("%Y-%m-%d"),
            }]), ["ticker", "business_ja", "employees", "dividend_policy",
                  "policy_flags", "policy_score", "updated_at"])
            n_ok += 1

        with connect() as conn:
            conn.execute("UPDATE edinet_index SET fetched_at=? WHERE code=?",
                         (datetime.now().isoformat(timespec="seconds"), r.code))

        if i % 25 == 0:
            rate = i / max(time.time() - t0, 1)
            remain = (len(idx) - i) / max(rate, 1e-6)
            _log(f"  {i}/{len(idx)} 成功 {n_ok} / 失敗 {n_ng}（残り約 {remain / 60:.0f} 分）")
        time.sleep(0.2)   # EDINET に負荷をかけない

    _log(f"取得完了: 成功 {n_ok} / 失敗 {n_ng} / 所要 {(time.time() - t0) / 60:.1f} 分")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="store_true", help="提出一覧を作り直す")
    ap.add_argument("--days", type=int, default=400, help="何日ぶん遡るか")
    ap.add_argument("--fetch", action="store_true", help="XBRL を取得する")
    ap.add_argument("--limit", type=int, default=None, help="先頭N社だけ")
    ap.add_argument("--all", action="store_true",
                    help="候補銘柄に絞らず、一覧にある全社を対象にする")
    args = ap.parse_args()

    init_db()
    if not args.index and not args.fetch:
        idx = read_df("SELECT COUNT(*) n, SUM(fetched_at IS NOT NULL) done FROM edinet_index")
        sm = read_df("SELECT COUNT(DISTINCT ticker) n FROM edinet_summary")
        print(f"一覧: {int(idx.n[0])} 社 / 取得済み {int(idx.done[0] or 0)} 社")
        print(f"指標が入っている銘柄: {int(sm.n[0])}")
        print("\n--index で一覧づくり、--fetch で取得します")
        return

    if args.index:
        build_index(args.days)
    if args.fetch:
        fetch_documents(args.limit, only_targets=not args.all)


if __name__ == "__main__":
    main()
