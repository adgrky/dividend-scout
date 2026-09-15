"""資金投入の配分エンジン。

「トレードで出た利益を移した。どこに入れるか」に答えるのが仕事。

【発掘スコア順に買わない】
発掘スコア（total）には「見過ごされ度」が入っている。市場に気づかれていないことは
**見つける理由**にはなっても、**いま買う理由**にはならない。
限られた資金の配り先を決めるときは、次の4つで優劣をつける。

    割安度        その銘柄自身の過去と比べて安いか（検証で唯一きれいに効いた）
    配当継続      買ったあと減配しないか
    目標到達      目標利回りに届いているか（届いていないなら待つのがヘム流）
    補完度        空いている業種・空いている配当月を埋めるか

重みは config.yaml の buy_priority。

最適化ソルバは使わない。上位候補から順に「入れられるか」を判定していくだけで
十分な精度が出るうえ、なぜその配分になったかを1行ずつ説明できる。
説明できない配分は使えない。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_LOT = 100  # 東証の売買単位（2018年以降は原則100株）


def _lot_size(price: float, budget: float) -> int:
    if not price or price <= 0:
        return 0
    return int(budget // (price * _LOT)) * _LOT


def buy_priority(cand: pd.DataFrame, positions: pd.DataFrame, config: dict,
                 month_gap: dict[int, float] | None = None) -> pd.DataFrame:
    """買い付け優先度（0〜100）と、その内訳を付けて返す。

    【なぜ加重和ではなく幾何平均か】
    加重和だと「片方が極端に高ければ、もう片方が低くても選ばれる」。
    実測すると『割安度80超・配当継続35未満』の銘柄が13件あり、加重和では
    69点で上位に入っていた（小松マテーレ 割安95・継続34 など）。
    配当投資では「安いが配当が危ない」も「安全だが高い」もどちらも買いたくない。
    **両方そろって初めて買う**という性質を、そのまま式にする。

        割安度100・継続100 → 100
        割安度100・継続25  →  50   （加重和なら68になってしまう）
        割安度60・継続60   →  60

    補完度は順位を大きく動かすものではなく、同点のときに
    「空いている業種・空いている配当月を埋めるほう」を選ぶための係数（±10%）。

    トラップ減点は必ず引く。実測で、引かないと買い付け上位50のうち7銘柄が
    高配当トラップ判定（最大19点）の銘柄だった。
    """
    cap = config["portfolio"]["max_sector_weight"]
    total_eval = positions["eval_value"].sum() if not positions.empty else 0.0
    sector_now = (positions.groupby("sector33")["eval_value"].sum()
                  if not positions.empty else pd.Series(dtype=float))

    c = cand.copy()

    # ── 割安度 ──
    # 「安い」には2つの意味がある。
    #   自己利回り順位 … その銘柄自身の過去と比べて安いか（買う時期の判断）
    #   絶対利回り順位 … 候補の中で利回りが高いか（いま受け取れる額）
    # 前者だけだと「いつも1.5%の銘柄が2.0%」を最高評価してしまい、
    # インカムとしては物足りない銘柄が上位に来る。両方を混ぜる。
    self_rank = (c["yield_percentile"].fillna(0.5) * 100).clip(0, 100)
    abs_rank = c["dividend_yield"].rank(pct=True) * 100
    c["_割安度"] = (0.6 * self_rank + 0.4 * abs_rank.fillna(50)).clip(0, 100)
    c["_自己利回り順位"] = self_rank
    c["_絶対利回り順位"] = abs_rank.fillna(50)

    # ── 配当の質 ──
    c["_継続"] = c["health"].fillna(0).clip(0, 100)

    # ── 補完度（タイブレーク）──
    if total_eval > 0:
        used = c["sector33"].map(sector_now).fillna(0.0)
        room = ((cap * total_eval - used) / (cap * total_eval)).clip(0, 1)
    else:
        room = pd.Series(1.0, index=c.index)
    if month_gap:
        def _month_score(s: str) -> float:
            months = [int(m.replace("月", "")) for m in str(s).split("・") if m]
            if not months:
                return 0.5
            return float(np.mean([month_gap.get(m, 0.5) for m in months]))
        month_fit = c["payout_months"].fillna("").map(_month_score)
    else:
        month_fit = pd.Series(0.5, index=c.index)
    c["_補完度"] = ((room + month_fit) / 2 * 100).clip(0, 100)

    # ── 目標到達度（表示用。順位には使わず、絞り込みの条件として使う）──
    # 目標利回りが結合されていない画面から呼ばれることもあるので、既定値で埋める
    if "target_yield" not in c.columns:
        c["target_yield"] = np.nan
    tgt = c["target_yield"].fillna(0.047).replace(0, np.nan)
    c["_目標到達"] = (c["dividend_yield"] / tgt * 100).clip(0, 200).fillna(0)

    base = np.sqrt(c["_割安度"].clip(lower=0) * c["_継続"].clip(lower=0))
    tiebreak = 0.9 + 0.2 * c["_補完度"] / 100
    c["買い付け優先度"] = (base * tiebreak - c.get("trap_penalty", 0).fillna(0)).clip(lower=0)
    return c


def buy_gate(c: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.Series]:
    """買ってはいけないものを外す。

    順位を付ける前の足切り。安いからといって、配当が危ない銘柄や
    高配当トラップの判定が出ている銘柄に資金を入れる理由はない。
    """
    g = config["buy_priority"]
    reasons = pd.Series("", index=c.index)
    trap = c.get("trap_penalty", pd.Series(0.0, index=c.index)).fillna(0)
    bad_trap = trap >= float(g["max_trap_penalty"])
    reasons[bad_trap] = "高配当トラップの判定が出ている"
    low_health = c["health"].fillna(0) < float(g["min_health"])
    reasons[low_health & (reasons == "")] = "配当継続スコアが低い"
    return c[reasons == ""].copy(), reasons


def month_gaps(positions: pd.DataFrame, calendar: pd.DataFrame | None) -> dict[int, float]:
    """月ごとの「受け取りの少なさ」を 0〜1 で返す。空いている月ほど1に近い。"""
    if calendar is None or calendar.empty:
        return {}
    amt = calendar.set_index("month")["amount"]
    peak = amt.max()
    if not peak:
        return {}
    return {int(m): float(1 - v / peak) for m, v in amt.items()}


def allocate(cash: float, candidates: pd.DataFrame, positions: pd.DataFrame,
             config: dict, max_names: int = 12,
             per_name_cap_pct: float = 0.25,
             require_below_target: bool = True,
             month_gap: dict[int, float] | None = None,
             allow_single_lot: bool = True) -> pd.DataFrame:
    """入金額を候補に割り振る。

    Parameters
    ----------
    max_names : int
        1回の投入で買う銘柄数の上限。ヘムは360〜400銘柄に超分散している。
        機械的な基準で選び、監視はアプリがやるのだから、絞る理由は薄い。
    per_name_cap_pct : float
        1銘柄あたりの投入上限（今回の入金額に対する比率）
    require_below_target : bool
        目標利回りに届いている銘柄だけを対象にする
    allow_single_lot : bool
        上限に収まらなくても、1単元だけなら買うことを許す

    【なぜ2周するか】
    日本株は100株単位でしか買えない。1単元の値段は10万〜70万円が普通なので、
    「1銘柄あたり入金の15%まで」といった上限を素直に当てると、単元がその上限を
    超える銘柄が全部落ちる。実測では、入金50万円（投入枠30万円）に対して
    **上位25銘柄のうち買えたのは3銘柄だけ**で、¥99,430 しか配れずに
    ¥200,570 が宙に浮いていた。しかも画面にはそれが「暴落用の現金」に見えていた。

    そこで2周する。
        1周目  上限を守って、分散を優先して配る
        2周目  余ったお金で、上限を超えても1単元だけなら買う
               （ただし1銘柄が入金の single_lot_max を超えることはしない）
    """
    if candidates is None or candidates.empty or cash <= 0:
        return pd.DataFrame()

    cap_sector = config["portfolio"]["max_sector_weight"]
    total_after = (positions["eval_value"].sum() if not positions.empty else 0.0) + cash
    sector_now = (positions.groupby("sector33")["eval_value"].sum()
                  if not positions.empty else pd.Series(dtype=float))

    c = candidates[candidates["last_close"].fillna(0) > 0].copy()
    if require_below_target and "target_yield" in c.columns:
        c = c[c["dividend_yield"].fillna(0) >= c["target_yield"].fillna(0)]
    if c.empty:
        return pd.DataFrame()

    c = buy_priority(c, positions, config, month_gap)
    c, _ = buy_gate(c, config)
    if c.empty:
        return pd.DataFrame()
    # 発掘スコアではなく買い付け優先度の順に配る
    c = c.sort_values("買い付け優先度", ascending=False)

    per_name_cap = cash * per_name_cap_pct
    single_lot_max = cash * float(config["buy_priority"].get("single_lot_max", 0.5))
    remaining = cash
    picks = []
    taken: set = set()

    def _try(r, cap_amount: float, relaxed: bool) -> bool:
        nonlocal remaining
        price = r["last_close"]
        lot_cost = price * _LOT
        if len(picks) >= max_names or remaining < lot_cost or r["ticker"] in taken:
            return False
        sector = r.get("sector33")
        used = float(sector_now.get(sector, 0.0)) + sum(
            p["投入額"] for p in picks if p["業種"] == sector)
        room = cap_sector * total_after - used
        if room <= 0 or lot_cost > room:
            return False
        shares = _lot_size(price, min(cap_amount, remaining, room))
        if shares < _LOT:
            if not relaxed or lot_cost > min(remaining, room, single_lot_max):
                return False
            shares = _LOT          # 上限は超えるが1単元だけ入れる
        amount = shares * price
        picks.append(_pick(r, sector, shares, amount, cap_sector * total_after - used,
                           relaxed and amount > cap_amount))
        taken.add(r["ticker"])
        remaining -= amount
        return True

    for _, r in c.iterrows():
        _try(r, per_name_cap, relaxed=False)
    if allow_single_lot:
        # 余ったお金で、上限を超えても1単元だけなら買う2周目
        for _, r in c.iterrows():
            if remaining <= 0 or len(picks) >= max_names:
                break
            _try(r, per_name_cap, relaxed=True)

    out = pd.DataFrame(picks)
    if not out.empty:
        out.attrs["残り"] = remaining
    return out


def _pick(r, sector, shares, amount, sector_room, relaxed: bool) -> dict:
    return {
        "ticker": r["ticker"],
        "コード": r.get("code"),
        "銘柄名": r.get("name"),
        "業種": sector,
        "株価": r["last_close"],
        "株数": shares,
        "投入額": amount,
        "利回り": r.get("dividend_yield"),
        "年間配当": amount * (r.get("dividend_yield") or 0),
        "買い付け優先度": r["買い付け優先度"],
        "割安度": r["_割安度"],
        "自己利回り順位": r["_自己利回り順位"],
        "絶対利回り順位": r["_絶対利回り順位"],
        "継続": r["_継続"],
        "目標到達": r["_目標到達"],
        "補完度": r["_補完度"],
        "トラップ減点": r.get("trap_penalty", 0),
        "発掘スコア": r.get("total"),
        "配当月": r.get("payout_months"),
        "業種の空き枠": max(sector_room, 0.0),
        "上限を超えて1単元": relaxed,
    }


def rebalance_funds(review: pd.DataFrame) -> float:
    """整理候補を売却した場合に作れる資金。"""
    if review is None or review.empty:
        return 0.0
    return float(review["eval_value"].sum())
