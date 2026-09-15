"""設定と共通パス。

Streamlit に依存しない。ヘッドレスなスクリプトからも同じ設定を読む。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

APP_DIR = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(APP_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
