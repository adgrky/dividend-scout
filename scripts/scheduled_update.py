"""自動更新の本体。毎週走らせるが、中身は「古くなったものだけ」やる。

【なぜ日付固定の3本立てにしないか】
「毎週土曜」「毎月1日」「毎年7月5日」と別々に登録すると、**その日に Mac が
落ちていたら1年ぶんまるごと飛ぶ**。しかも飛んだことに気づけない。

ここでは毎週1本だけ動かし、走るたびに「何が何日前か」を見て、
古くなったものだけ追加でやる。Mac が2週間落ちていても、次に起きたときに
足りないぶんが自動で埋まる。

    毎週   株価・配当・スコア      約10分
    月1回  会社の基本情報          約10分   （30日以上たっていたら）
    年1回  有価証券報告書          約70分   （180日以上たっていて、かつ7月以降）

有報を7月以降にするのは、3月期決算の会社の有報が **6月下旬に集中して出る**ため。
6月より前に取りに行っても去年のものしか無い。

使い方:
    uv run python scripts/scheduled_update.py            # 古いものだけやる
    uv run python scripts/scheduled_update.py --dry-run  # 何をやるかだけ見る
    uv run python scripts/scheduled_update.py --all      # 全部やる
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
LOCK = ROOT / "data" / ".update.lock"


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def age_days(sql: str) -> int | None:
    from modules.store import read_df
    try:
        v = read_df(sql).iloc[0, 0]
    except Exception:
        return None
    if not v:
        return None
    try:
        return (date.today() - date.fromisoformat(str(v)[:10])).days
    except ValueError:
        return None


def plan(config: dict, force_all: bool = False) -> list[tuple[str, list[str], str]]:
    """いま何をやるべきかを決める。"""
    s = config.get("schedule", {})
    jobs: list[tuple[str, list[str], str]] = []

    jobs.append(("株価・配当・スコア", [PY, str(ROOT / "scripts" / "weekly_scan.py")],
                 "毎週やる"))

    a = age_days("SELECT MAX(updated_at) FROM company_profile")
    limit = int(s.get("profiles_days", 30))
    if force_all or a is None or a >= limit:
        jobs.append(("会社の基本情報",
                     [PY, str(ROOT / "scripts" / "fetch_profiles.py"), "--missing-only"],
                     f"前回から {a if a is not None else '—'} 日（{limit}日で更新）"))

    a = age_days("SELECT MAX(fetched_at) FROM edinet_index")
    limit = int(s.get("edinet_days", 180))
    month_ok = date.today().month >= int(s.get("edinet_from_month", 7))
    if force_all or ((a is None or a >= limit) and month_ok):
        jobs.append(("有価証券報告書",
                     [PY, str(ROOT / "scripts" / "fetch_edinet.py"), "--index", "--fetch"],
                     f"前回から {a if a is not None else '—'} 日（{limit}日・7月以降で更新）"))
    return jobs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="何をやるかだけ出す")
    ap.add_argument("--all", action="store_true", help="古さに関係なく全部やる")
    args = ap.parse_args()

    from modules.config import load_config
    from modules.store import connect, init_db
    init_db()
    config = load_config()

    jobs = plan(config, force_all=args.all)
    _log(f"やること {len(jobs)} 件")
    for name, _, why in jobs:
        _log(f"  ・{name}（{why}）")
    if args.dry_run:
        return 0

    # 手で更新している最中に自動更新が重なると、同じデータを二重に取りに行く。
    # 走っているあいだは鍵をかけ、あとから来たほうは黙って降りる。
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3 * 3600:
        _log("ほかの更新が走っているので、今回は何もしません")
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))

    started = datetime.now()
    done, failed = [], []
    try:
        for name, cmd, _ in jobs:
            _log(f"▶ {name} を更新中…")
            t0 = time.time()
            try:
                r = subprocess.run(cmd, cwd=ROOT, timeout=6 * 3600)
                if r.returncode == 0:
                    done.append(f"{name}（{(time.time() - t0) / 60:.0f}分）")
                    _log(f"  ✅ {name} 完了 {(time.time() - t0) / 60:.1f}分")
                else:
                    failed.append(f"{name}（終了コード {r.returncode}）")
                    _log(f"  ⚠️ {name} 失敗 終了コード {r.returncode}")
            except subprocess.TimeoutExpired:
                failed.append(f"{name}（時間切れ）")
                _log(f"  ⚠️ {name} 時間切れ")
            except Exception as exc:
                failed.append(f"{name}（{exc}）")
                _log(f"  ⚠️ {name} 失敗 {exc}")
    finally:
        LOCK.unlink(missing_ok=True)

    note = "完了: " + " / ".join(done) if done else "完了したものなし"
    if failed:
        note += "　／　失敗: " + " / ".join(failed)
    with connect() as conn:
        conn.execute(
            "INSERT INTO scan_runs (started_at, finished_at, kind, n_universe, n_passed, note)"
            " VALUES (?, ?, ?, 0, 0, ?)",
            (started.isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds"),
             "scheduled_failed" if failed else "scheduled", note))

    _log(note)
    _log(f"合計 {(datetime.now() - started).total_seconds() / 60:.1f}分")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
