"""検証15 — NISA枠に何を入れるべきか。

【制度の確認（2026-09-16 に裏取り）】
    成長投資枠  年 240万円 ／ 生涯 1,200万円（NISA全体 1,800万円のうち）
    売却すると **翌年1月1日** に **簿価（取得価格）ぶん** の生涯枠が復活する
    年間投資枠（240万円）は復活しない
    出典: https://www.soico.jp/no1/news/securities/5078
          https://www.soico.jp/no1/news/securities/8996

【考え方】
売らずに持ち続ける前提なら、NISAの価値は **配当への課税 20.315% が消えること**
にほぼ尽きる（譲渡益の非課税は、売らないかぎり実現しない）。

    枠 1円あたりの毎年の節税 ＝ その銘柄の現在利回り × 20.315%

つまり **枠には利回りが高い銘柄を入れる**。それだけ。ややこしいのは
「すでに特定口座で持っているものを移すか」のほう。移すには一度売るので、
含み益に税がかかる。

    移すコスト（1回だけ）   ＝ 含み益 × 20.315%
    移す便益（毎年）        ＝ 年間配当 × 20.315%
    回収にかかる年数        ＝ 含み益 ÷ 年間配当

**含み損の銘柄は話が逆になる。** 売れば損失が確定して、同じ年の他の利益と
損益通算できる。移すコストがマイナス（＝得）になる。

使い方:
    uv run python scripts/validate_nisa.py
    uv run python scripts/validate_nisa.py --horizon 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                           # noqa: E402
import pandas as pd                          # noqa: E402

from modules import valreport as V           # noqa: E402
from modules.config import load_config       # noqa: E402
from modules.format import yen               # noqa: E402
from modules.portfolio import load_positions  # noqa: E402

TAX = 0.20315
GROWTH_LIFETIME = 12_000_000    # 成長投資枠の生涯上限
GROWTH_ANNUAL = 2_400_000       # 成長投資枠の年間上限


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=20, help="何年持ち続ける前提か")
    a = ap.parse_args()
    cfg = load_config()
    pos = load_positions(cfg)
    if pos.empty:
        print("保有がありません")
        return
    T = a.horizon

    nisa = pos[pos["account"] == "nisa"]
    spec = pos[pos["account"] != "nisa"]

    print(f"検証15 — NISA枠に何を入れるべきか（{T}年持ち続ける前提）")
    V.hr("1. いまの状態")
    for name, d in (("NISA", nisa), ("特定口座", spec)):
        if d.empty:
            continue
        y = d["annual_dividend"].sum() / d["eval_value"].sum()
        print(f"  {name:<8s} {len(d):>3d}銘柄  簿価 {yen(d['cost_value'].sum()):>12s}"
              f"  時価 {yen(d['eval_value'].sum()):>12s}"
              f"  年間配当 {yen(d['annual_dividend'].sum()):>10s}"
              f"  利回り {y:.2%}")
    used = float(nisa["cost_value"].sum())
    room = GROWTH_LIFETIME - used
    print(f"\n  成長投資枠の残り: {yen(room)}（生涯 {yen(GROWTH_LIFETIME)} − 簿価 {yen(used)}）")
    print(f"  ただし1年に入れられるのは {yen(GROWTH_ANNUAL)} まで。"
          f"埋めきるのに最短 {np.ceil(room / GROWTH_ANNUAL):.0f} 年")
    print(f"\n  いま特定口座で払っている配当への税: 年 {yen(spec['annual_dividend'].sum() * TAX)}")
    print(f"  {T}年ぶんだと {yen(spec['annual_dividend'].sum() * TAX * T)}"
          f"（増配を考えなければ、の話）")

    V.hr("2. 特定口座の銘柄を、いまNISAに移すと得か")
    s = spec.copy()
    s["含み益"] = s["eval_value"] - s["cost_value"]
    s["移す税"] = s["含み益"].clip(lower=0) * TAX
    s["損出しの戻り"] = (-s["含み益"]).clip(lower=0) * TAX
    s["毎年の節税"] = s["annual_dividend"] * TAX
    s["回収年数"] = np.where(s["毎年の節税"] > 0,
                          (s["移す税"] - s["損出しの戻り"]) / s["毎年の節税"], np.inf)
    s[f"{T}年の得"] = s["毎年の節税"] * T - s["移す税"] + s["損出しの戻り"]
    s["枠あたりの得"] = s[f"{T}年の得"] / s["eval_value"]

    good = s[s[f"{T}年の得"] > 0].sort_values("枠あたりの得", ascending=False)
    bad = s[s[f"{T}年の得"] <= 0]
    print(f"  {T}年持つ前提で、移すと得になる銘柄 {len(good)} / {len(s)}")
    print(f"  全部移したときの {T}年の得: {yen(good[f'{T}年の得'].sum())}"
          f"（必要な枠 {yen(good['eval_value'].sum())}）")
    if len(bad):
        print(f"  移すと損になる銘柄 {len(bad)}（含み益が大きいわりに配当が少ない）")

    print(f"\n  枠が {yen(room)} しかないので、効率の良い順に埋める")
    room_left, n_picked, gain = room, 0, 0.0
    for _, r in good.iterrows():
        if r["eval_value"] <= room_left:
            n_picked += 1
            room_left -= r["eval_value"]
            gain += r[f"{T}年の得"]
    print(f"  → {n_picked}銘柄を移すと、{T}年で {yen(gain)} の得。残り枠 {yen(room_left)}")

    show = good.head(15)[["code", "name_jpx", "eval_value", "含み益",
                          "annual_dividend", "回収年数", f"{T}年の得"]].copy()
    show["回収年数"] = show["回収年数"].map(
        lambda v: "即得" if v <= 0 else (f"{v:.1f}年" if np.isfinite(v) else "—"))
    show.columns = ["コード", "銘柄", "時価", "含み益", "年間配当", "回収年数", f"{T}年の得"]
    for c in ("時価", "含み益", "年間配当", f"{T}年の得"):
        show[c] = show[c].round(0).astype(int)
    print(f"\n  移す価値が高い順（上位15）")
    print(show.to_string(index=False))
    print()

    V.hr("3. 新しく買うお金は、どちらの口座に入れるべきか")
    print("  答えははっきりしている。**NISAが空いているかぎり、新規買いは全部NISA。**")
    print("  移し替えと違って含み益への課税が無いので、コストがゼロ。")
    print(f"\n  いまの枠の残り {yen(room)} を新規資金だけで埋めるとして、")
    for m in (50_000, 100_000, 200_000):
        yrs = room / (m * 12)
        print(f"    毎月 {yen(m)} なら {yrs:.1f}年（年間枠 {yen(GROWTH_ANNUAL)} には収まる）")
    print("\n  埋まるまでの間、特定口座で払う税は毎年減っていくので、")
    print("  **移し替えを考える前に、まず新規資金でどこまで埋まるかを見るべき。**")

    V.hr("4. NISAの中身は、利回りが高い順になっているか")
    both = pos.copy()
    both["現在利回り"] = both["annual_dividend"] / both["eval_value"]
    cut = both["現在利回り"].quantile(0.5)
    tbl = both.groupby([both["account"], both["現在利回り"] > cut]).agg(
        銘柄数=("ticker", "count"), 時価=("eval_value", "sum"),
        年間配当=("annual_dividend", "sum"))
    tbl.index = [f"{'NISA' if a == 'nisa' else '特定'}／利回り{'高' if b else '低'}"
                 for a, b in tbl.index]
    tbl["利回り"] = (tbl["年間配当"] / tbl["時価"] * 100).round(2)
    print(tbl.assign(時価=lambda x: x.時価.round(0),
                     年間配当=lambda x: x.年間配当.round(0)).to_string())
    lo_nisa = both[(both["account"] == "nisa") & (both["現在利回り"] <= cut)]
    hi_spec = both[(both["account"] != "nisa") & (both["現在利回り"] > cut)]
    print(f"\n  NISAにある低利回り銘柄 {len(lo_nisa)}（時価 {yen(lo_nisa['eval_value'].sum())}）")
    print(f"  特定にある高利回り銘柄 {len(hi_spec)}（時価 {yen(hi_spec['eval_value'].sum())}）")
    print("\n  入れ替えれば枠の効率は上がるが、NISAの銘柄を売っても枠が戻るのは**翌年**。")
    print("  そして年間枠240万円は復活しない。急ぐ理由は無い。")

    V.hr("5. 年240万円の枠を、新規買いと移し替えでどう分けるか")
    print("  **移し替えと新規買いは、同じ年240万円を奪い合う。** ここが要点。")
    print(f"  {'毎月の入金':>10s}{'年の入金':>12s}{'移し替えに回せる':>16s}"
          f"{'何年で枠を使い切るか':>22s}")
    for m in (50_000, 100_000, 150_000, 200_000):
        yearly = m * 12
        left = max(0, GROWTH_ANNUAL - yearly)
        total_need = room
        yrs = total_need / GROWTH_ANNUAL if left > 0 else total_need / yearly
        print(f"  {yen(m):>10s}{yen(yearly):>12s}{yen(left):>16s}{yrs:>19.1f}年")
    print("\n  年240万を使い切れるだけ入金していないなら、余った枠で移し替える。")
    print("  移し替えの順番は上の表（枠あたりの得が大きい順）。")

    V.hr("6. 含み損の銘柄について（注意）")
    loss = s[s["含み益"] < 0]
    if loss.empty:
        print("  含み損の銘柄はありません。")
    else:
        print(f"  含み損の銘柄 {len(loss)}（合計 {yen(-loss['含み益'].sum())} の損）")
        print(f"  売ると {yen(loss['損出しの戻り'].sum())} の税が戻る計算にしているが、"
              "\n  **これは同じ年に他で利益を確定している場合だけ**。")
        print("  利益が無ければ、損失は確定申告で3年繰り越すことになる。")
        print("  繰り越すつもりが無いなら、この戻りは無いものとして考えたほうが安全。")
        alt = (loss["毎年の節税"] * T).sum()
        print(f"  戻りを数えなくても、含み損の銘柄を移す {T}年の得は {yen(alt)}"
              "（コストがゼロなので、どのみち移す価値がある）")


if __name__ == "__main__":
    main()
