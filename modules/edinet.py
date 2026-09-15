"""EDINET（金融庁の電子開示システム）から有価証券報告書を取る。

【なぜ必要か】
yfinance の財務は4〜5期分しか返らず、しかも日本基準の指標（配当性向・自己資本比率）は
欠損や誤りがある（実測で配当性向480%という値を確認）。増配の持続性を判定するには
正確な長期データが要る。

【なぜ有報が効率的か】
有価証券報告書の冒頭「主要な経営指標等の推移」には、**5年分**の
    1株当たり当期純利益 / 1株当たり配当額 / 配当性向 / 自己資本利益率 /
    自己資本比率 / 営業活動によるキャッシュ・フロー / 売上高 / 従業員数
が構造化データ（XBRL）で入っている。1社1ファイル（約300KB）で5年分が揃う。
さらに「事業の内容」が日本語で入っているので、何をやっている会社かも取れる。

APIは無料。利用にはアカウント登録とAPIキーが必要（環境変数 EDINET_API_KEY）。
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_BASE = "https://api.edinet-fsa.go.jp/api/v2"
_UA = {"User-Agent": "dividend-scout/1.0"}
_DOC_TYPE_YUHO = "120"          # 有価証券報告書

# 「主要な経営指標等の推移」の要素名 → こちらでの列名
_SUMMARY_ELEMENTS = {
    "NetSalesSummaryOfBusinessResults": "sales",
    "OrdinaryIncomeLossSummaryOfBusinessResults": "ordinary_income",
    "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults": "net_income",
    "NetIncomeLossSummaryOfBusinessResults": "net_income",
    "BasicEarningsLossPerShareSummaryOfBusinessResults": "eps",
    "DividendPaidPerShareSummaryOfBusinessResults": "dps",
    "PayoutRatioSummaryOfBusinessResults": "payout_ratio",
    "RateOfReturnOnEquitySummaryOfBusinessResults": "roe",
    "EquityToAssetRatioSummaryOfBusinessResults": "equity_ratio",
    "NetAssetsSummaryOfBusinessResults": "net_assets",
    "TotalAssetsSummaryOfBusinessResults": "total_assets",
    "NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults": "operating_cf",
    "NumberOfEmployees": "employees",
}

# contextRef の接頭辞 → 何年前か
_PERIOD_OFFSET = {
    "CurrentYear": 0, "Prior1Year": 1, "Prior2Year": 2,
    "Prior3Year": 3, "Prior4Year": 4,
}


class EdinetError(RuntimeError):
    pass


def api_key() -> str:
    """APIキーを環境変数から取る。無ければ stock-recommender の .env も見る。"""
    key = os.getenv("EDINET_API_KEY")
    if key:
        return key
    # 同じキーを2箇所で管理したくないので、既に登録済みの場所を参照する
    fallback = Path.home() / "dev" / "stock-recommender" / ".env"
    if fallback.exists():
        for line in fallback.read_text(encoding="utf-8").splitlines():
            if line.startswith("EDINET_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise EdinetError(
        "EDINET_API_KEY が設定されていません。\n"
        "  https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1 で無料登録し、\n"
        "  このプロジェクトの .env に EDINET_API_KEY=... を書いてください。"
    )


def _get(path: str, params: dict, timeout: int = 180) -> bytes:
    params = {**params, "Subscription-Key": api_key()}
    url = f"{_BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def list_documents(day: date, retries: int = 2) -> list[dict]:
    """その日に提出された有価証券報告書（上場企業のみ）を返す。"""
    for attempt in range(retries + 1):
        try:
            data = json.loads(_get("documents.json",
                                   {"date": day.isoformat(), "type": "2"}, timeout=60))
            break
        except Exception:
            if attempt == retries:
                return []
            time.sleep(2 * (attempt + 1))
    out = []
    for r in data.get("results") or []:
        if r.get("docTypeCode") != _DOC_TYPE_YUHO:
            continue
        sec = r.get("secCode")
        if not sec:
            continue
        out.append({
            # secCode は5桁（証券コード4桁＋末尾0）。末尾を落として4桁に戻す。
            "code": str(sec)[:4],
            "doc_id": r["docID"],
            "filer_name": r.get("filerName"),
            "period_end": r.get("periodEnd"),
            "submit_date": day.isoformat(),
        })
    return out


def build_index(days_back: int = 400, end: date | None = None,
                progress=None) -> pd.DataFrame:
    """直近N日ぶんの提出一覧を舐めて、銘柄コード→有報 のひも付けを作る。

    有報は決算期末から3ヶ月以内に出る。3月決算が大半なので6月に集中するが、
    他の決算期もあるため1年ぶんを見る。
    """
    end = end or date.today()
    rows = []
    for i in range(days_back):
        day = end - timedelta(days=i)
        if progress and i % 20 == 0:
            progress(i / days_back, f"提出一覧 {day} まで遡り中（{len(rows)} 件）")
        rows.extend(list_documents(day))
        time.sleep(0.15)   # EDINET に負荷をかけない
    if progress:
        progress(1.0, f"提出一覧 完了（{len(rows)} 件）")
    if not rows:
        return pd.DataFrame(columns=["code", "doc_id", "filer_name", "period_end", "submit_date"])
    df = pd.DataFrame(rows)
    # 同じ会社が複数年ぶん出てくるので、期末が新しいものを残す
    return df.sort_values("period_end").drop_duplicates("code", keep="last").reset_index(drop=True)


def download_xbrl(doc_id: str, retries: int = 2) -> str | None:
    """有報のXBRL本体（テキスト）を取る。"""
    for attempt in range(retries + 1):
        try:
            blob = _get(f"documents/{doc_id}", {"type": "1"})
            break
        except Exception:
            if attempt == retries:
                return None
            time.sleep(3 * (attempt + 1))
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return None
    cands = [n for n in z.namelist() if n.endswith(".xbrl") and "PublicDoc" in n]
    if not cands:
        return None
    return z.read(cands[0]).decode("utf-8", "ignore")


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t　]+")


def parse_business(raw: str, limit: int = 600) -> str:
    """「事業の内容」を日本語の平文で取り出す。"""
    m = re.search(r"<jpcrp[^ >]*:DescriptionOfBusinessTextBlock[^>]*>(.*?)</jpcrp[^ >]*:"
                  r"DescriptionOfBusinessTextBlock>", raw, re.S)
    if not m:
        return ""
    import html
    text = html.unescape(m.group(1))
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).replace("\n", " ").strip()
    return text[:limit]


# 配当方針のキーワード。日本株の「増配意思」は、実績よりも会社が何を約束したかに出る。
# 累進配当（減らさないと約束）や DOE（純資産に対して配当を決める）を掲げた会社は、
# 利益が一時的に落ちても配当を維持・増加させる圧力が内側から働く。
_POLICY_PATTERNS = {
    "累進配当": (r"累進(的)?配当|減配(は)?(行わ|いたしま|しま)(ない|せん)|前年度の配当金を下限",
               0.30),
    "DOE（純資産配当率）": (r"DOE|株主資本配当率|純資産配当率", 0.25),
    "配当性向の目標": (r"配当性向[^。]{0,20}?(\d{2})\s*[%％][^。]{0,10}?(以上|目標|とし|を目指)", 0.20),
    "連続増配を明言": (r"連続(して)?増配|増配を(継続|続け)", 0.15),
    "安定配当": (r"安定(的)?(な|に)?配当", 0.05),
    "自己株式の取得": (r"自己株式の取得|自社株買い", 0.05),
}


def parse_dividend_policy(raw: str, limit: int = 500) -> tuple[str, dict, float]:
    """「配当政策」の本文と、そこから読み取れる方針のフラグ・スコアを返す。

    Returns
    -------
    (本文, {方針名: True}, 0〜1のスコア)
    """
    m = re.search(r"<jpcrp[^ >]*:DividendPolicyTextBlock[^>]*>(.*?)"
                  r"</jpcrp[^ >]*:DividendPolicyTextBlock>", raw, re.S)
    if not m:
        return "", {}, 0.0
    import html
    text = html.unescape(m.group(1))
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).replace("\n", " ").strip()

    flags, score = {}, 0.0
    for label, (pattern, weight) in _POLICY_PATTERNS.items():
        if re.search(pattern, text):
            flags[label] = True
            score += weight
    return text[:limit], flags, min(score, 1.0)


def parse_summary(raw: str, period_end: str) -> pd.DataFrame:
    """「主要な経営指標等の推移」から5年分の指標を取り出す。

    連結（contextRef に Member が付かない）を優先し、無ければ単体を使う。
    """
    end_year = int(str(period_end)[:4]) if period_end else None
    records: dict[int, dict] = {}

    for element, col in _SUMMARY_ELEMENTS.items():
        pattern = rf'<jpcrp[^ >]*:{element}[^>]*contextRef="([^"]+)"[^>]*>([^<]*)<'
        for ctx, val in re.findall(pattern, raw):
            prefix = None
            for p in _PERIOD_OFFSET:
                if ctx.startswith(p):
                    prefix = p
                    break
            if prefix is None:
                continue
            consolidated = "NonConsolidatedMember" not in ctx
            offset = _PERIOD_OFFSET[prefix]
            year = (end_year - offset) if end_year else -offset
            try:
                num = float(str(val).replace(",", "").strip())
            except ValueError:
                continue
            rec = records.setdefault(year, {"fiscal_year": year, "_consolidated": consolidated})
            # 連結が来たら単体を上書きする。逆はしない。
            if col not in rec or (consolidated and not rec.get("_consolidated_" + col, False)):
                rec[col] = num
                rec["_consolidated_" + col] = consolidated

    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records.values())
    df = df[[c for c in df.columns if not c.startswith("_")]]
    return df.sort_values("fiscal_year").reset_index(drop=True)


def fetch_company(doc_id: str, period_end: str) -> dict:
    """1社ぶんの5年指標・事業内容・配当方針をまとめて取る。"""
    raw = download_xbrl(doc_id)
    if raw is None:
        return {"summary": pd.DataFrame(), "business": "", "policy": "",
                "policy_flags": {}, "policy_score": 0.0}
    policy, flags, score = parse_dividend_policy(raw)
    return {
        "summary": parse_summary(raw, period_end),
        "business": parse_business(raw),
        "policy": policy,
        "policy_flags": flags,
        "policy_score": score,
    }
