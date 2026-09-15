"""SQLite 永続化層。

ORM は使わず生 SQL。DB はローカルが唯一の正で、保有情報は外に出さない。

テーブルの役割:
    universe      JPX 由来の全上場銘柄（コード・銘柄名・33業種・市場区分）
    prices        週足の終値と出来高。全市場3,700銘柄の日足を持つと2,000万行を超えるため
                  週足に落とす。利回りパーセンタイルもバリュエーション履歴も週足で足りる
    quotes        銘柄ごとの最新値。日足から計算した52週レンジ・20日平均売買代金など
    dividends     配当落ち日ベースの1回ごとの配当額（分割調整済み）
    splits        株式分割・併合（DPS を遡って調整するために必要）
    fundamentals  財務5期分。絞り込み後の銘柄だけ埋まる
    scores        日付つきスナップショット。検証の生命線なので上書きしない
    holdings      保有（口座別）
    transactions  売買記録
    watchlist     監視銘柄と目標利回り
    alerts        監視が拾った異変
    settings      key-value
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from modules.config import db_path

_DDL = """
CREATE TABLE IF NOT EXISTS universe (
    ticker      TEXT PRIMARY KEY,   -- 9432.T
    code        TEXT NOT NULL,      -- 9432
    name        TEXT,
    sector33    TEXT,               -- JPX の 33業種区分（日本語）
    market      TEXT,               -- プライム（内国株式）等
    scale       TEXT,               -- 規模区分
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS prices (          -- 週足
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,                    -- その週の最終営業日
    close   REAL,
    volume  REAL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);

CREATE TABLE IF NOT EXISTS quotes (
    ticker          TEXT PRIMARY KEY,
    asof            TEXT,
    last_close      REAL,
    high_52w        REAL,
    low_52w         REAL,
    pos_52w         REAL,    -- 52週レンジ内の位置 0=安値 1=高値
    avg_turnover    REAL,    -- 20日平均売買代金（円）
    ret_1y          REAL,
    listing_start   TEXT,    -- 株価データの最初の日（上場経過年数の代用）
    n_bars          INTEGER
);

CREATE TABLE IF NOT EXISTS dividends (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,          -- 権利落ち日
    amount  REAL NOT NULL,          -- 1株あたり（分割調整済み）
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS splits (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    ratio   REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker          TEXT NOT NULL,
    fiscal_end      TEXT NOT NULL,  -- 決算期末 YYYY-MM-DD
    net_income      REAL,
    revenue         REAL,
    operating_income REAL,
    operating_cf    REAL,
    free_cf         REAL,
    total_equity    REAL,
    total_assets    REAL,
    total_debt      REAL,
    cash            REAL,
    shares          REAL,
    PRIMARY KEY (ticker, fiscal_end)
);

CREATE TABLE IF NOT EXISTS snapshots (
    ticker      TEXT NOT NULL,
    asof        TEXT NOT NULL,      -- スナップショット日
    market_cap  REAL,
    per         REAL,
    pbr         REAL,
    roe         REAL,
    payout_ratio REAL,
    held_pct_institutions REAL,
    PRIMARY KEY (ticker, asof)
);

CREATE TABLE IF NOT EXISTS scores (
    ticker      TEXT NOT NULL,
    asof        TEXT NOT NULL,
    total       REAL,
    capacity    REAL,
    willingness REAL,
    growth      REAL,
    neglect     REAL,
    valuation   REAL,
    trap_penalty REAL,
    gate_passed INTEGER,
    gate_reason TEXT,
    detail_json TEXT,               -- 各指標の生値。カルテで内訳を開示するため
    PRIMARY KEY (ticker, asof)
);

CREATE TABLE IF NOT EXISTS holdings (
    account     TEXT NOT NULL,      -- specific / nisa
    ticker      TEXT NOT NULL,
    name        TEXT,
    shares      REAL NOT NULL,
    avg_cost    REAL,
    target_yield REAL,
    bottom_yield REAL,
    updated_at  TEXT,
    PRIMARY KEY (account, ticker)
);

CREATE TABLE IF NOT EXISTS transactions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    date    TEXT NOT NULL,
    account TEXT,
    ticker  TEXT,
    name    TEXT,
    type    TEXT,                   -- buy / sell / dividend
    shares  REAL,
    price   REAL,
    fee     REAL,
    memo    TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    ticker      TEXT PRIMARY KEY,
    name        TEXT,
    target_yield REAL,
    bottom_yield REAL,
    source      TEXT,               -- manual / scout（発掘由来）
    thesis      TEXT,               -- なぜ増配が続くと考えるか
    invalidation TEXT,              -- 棄却条件
    added_at    TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    ticker      TEXT,
    severity    TEXT,               -- high / medium / low
    kind        TEXT,               -- dividend_cut / ocf_decline / limit_reached ...
    message     TEXT,
    resolved    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    kind        TEXT,               -- weekly / daily
    n_universe  INTEGER,
    n_passed    INTEGER,
    note        TEXT
);
"""


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path or db_path()))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(_DDL)


def upsert_df(table: str, df: pd.DataFrame, columns: Iterable[str],
              path: Path | None = None, chunk: int = 5000) -> int:
    """DataFrame を INSERT OR REPLACE でまとめて書き込む。"""
    columns = list(columns)
    if df is None or df.empty:
        return 0
    sub = df.reindex(columns=columns)
    placeholders = ",".join("?" * len(columns))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    rows = [tuple(None if pd.isna(v) else v for v in rec) for rec in sub.itertuples(index=False, name=None)]
    with connect(path) as conn:
        for i in range(0, len(rows), chunk):
            conn.executemany(sql, rows[i:i + chunk])
    return len(rows)


def read_df(sql: str, params: tuple = (), path: Path | None = None) -> pd.DataFrame:
    with connect(path) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def get_setting(key: str, default=None, path: Path | None = None):
    with connect(path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def set_setting(key: str, value, path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (key, json.dumps(value, ensure_ascii=False)),
        )
