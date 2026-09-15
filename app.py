"""dividend-scout — 増配期待株の発掘エンジン。

このアプリの本筋は「発掘」。市場に正しく評価されていない増配期待企業を
全上場から探して提示する。保有管理と資金配分は、発掘した銘柄を
「いくらで・どの枠に入れるか」を決めるための受け皿。

ロジックはすべて modules/ 側にあり、ここは画面の入口だけを持つ。
同じ関数を GitHub Actions からも呼ぶので、画面と通知の結果がズレない。
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="dividend-scout", page_icon="🔭", layout="wide")

from modules.config import bridge_secrets_to_env   # noqa: E402

bridge_secrets_to_env()

pages = [
    st.Page("pages/1_discover.py", title="発掘", icon="🔭", default=True),
    st.Page("pages/2_profile.py", title="銘柄カルテ", icon="📄"),
    st.Page("pages/3_monitor.py", title="監視", icon="🚨"),
    st.Page("pages/4_allocate.py", title="資金投入", icon="💰"),
    st.Page("pages/5_portfolio.py", title="ポートフォリオ", icon="📊"),
    st.Page("pages/6_validate.py", title="検証", icon="🧪"),
    st.Page("pages/7_guide.py", title="使い方", icon="📖"),
]
st.navigation(pages).run()
