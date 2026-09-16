"""設定と共通パス。

Streamlit に依存しない。ヘッドレスなスクリプトからも同じ設定を読む。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

APP_DIR = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4)
def _read_config(_stamp: float) -> dict:
    with open(APP_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config() -> dict:
    """設定を読む。**config.yaml を書き換えたら、その場で効く。**

    以前はプロセスのあいだ1回だけ読んで固めていた。そのせいで、閾値や重みを
    直してもアプリを再起動するまで反映されず、しかも画面には何の手がかりも
    出なかった（実測: 大人買いラインの段を書き換えたのに、古い段が表示され続けた）。
    「コードに数値を直書きしない」と決めている以上、設定を直したら効かないと困る。

    ファイルの更新時刻をキーにして読み直す。中身が変わっていなければ
    キャッシュがそのまま返るので、読み込みの負担は増えない。
    """
    try:
        stamp = (APP_DIR / "config.yaml").stat().st_mtime
    except OSError:
        stamp = 0.0
    return _read_config(stamp)


def db_path(config: dict | None = None) -> Path:
    config = config or load_config()
    p = APP_DIR / config["paths"]["db"]
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def legacy_dir(config: dict | None = None) -> Path:
    config = config or load_config()
    return (APP_DIR / config["paths"]["legacy_dir"]).resolve()


def bridge_secrets_to_env() -> None:
    """Streamlit の secrets を os.environ に流し込む。

    これを噛ませることで、modules/ 以下は一切 Streamlit を import せずに済み、
    GitHub Actions からも同じコードが動く。
    secrets.toml が存在しない環境で st.secrets に触ると警告が出るため、
    ファイルの存在を先に確認する。
    """
    paths = [Path.home() / ".streamlit" / "secrets.toml", APP_DIR / ".streamlit" / "secrets.toml"]
    if not any(p.exists() for p in paths):
        return
    try:
        import streamlit as st
    except ImportError:
        return
    for key in ("EDINET_API_KEY", "NTFY_TOPIC"):
        if key not in os.environ:
            try:
                if key in st.secrets:
                    os.environ[key] = str(st.secrets[key])
            except Exception:
                pass
