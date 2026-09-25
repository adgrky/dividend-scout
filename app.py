"""DIVA — 増配期待株の発掘エンジン。

このアプリの本筋は「発掘」。市場に正しく評価されていない増配期待企業を
全上場から探して提示する。保有管理と資金配分は、発掘した銘柄を
「いくらで・どの枠に入れるか」を決めるための受け皿。

ロジックはすべて modules/ 側にあり、ここは画面の入口だけを持つ。
同じ関数を GitHub Actions からも呼ぶので、画面と通知の結果がズレない。
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="DIVA", page_icon="🔭", layout="wide")

from modules.config import bridge_secrets_to_env   # noqa: E402
from modules.store import init_db                  # noqa: E402

bridge_secrets_to_env()

# テーブルの作成と列の追加（マイグレーション）をここで必ず走らせる。
# これまでスクリプト側でしか呼んでおらず、列を足したあとアプリだけを起動すると
# 「no such column」で画面が落ちた（実測: ref_date を足したあとポートフォリオ画面が
# 落ちる状態になっていた）。何度呼んでも安全で、一瞬で終わる。
init_db()

from modules.ui import freshness_banner                # noqa: E402

pages = [
    st.Page("views/1_discover.py", title="発掘", icon="🔭", default=True),
    st.Page("views/2_profile.py", title="銘柄カルテ", icon="📄"),
    st.Page("views/3_monitor.py", title="監視", icon="🚨"),
    st.Page("views/4_allocate.py", title="資金投入", icon="💰"),
    st.Page("views/5_portfolio.py", title="ポートフォリオ", icon="📊"),
    st.Page("views/6_validate.py", title="検証", icon="🧪"),
    st.Page("views/7_guide.py", title="使い方", icon="📖"),
]
nav = st.navigation(pages)

# データがいつ時点のものかは、どの画面にいても見えていなければならない。
# 古い株価のまま利回りや指値を信じて売買を決めてしまうのを防ぐ。
freshness_banner()

nav.run()
