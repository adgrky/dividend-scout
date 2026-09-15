"""ntfy.sh へのプッシュ通知。アカウント登録もAPIキーも要らない。

トピック名を知っている人は誰でも購読できるので、推測されにくい文字列にすること。
環境変数 NTFY_TOPIC で設定する。
"""
from __future__ import annotations

import os

import requests


def send(title: str, message: str, topic: str | None = None,
         priority: str = "default", tags: str = "chart_with_upwards_trend") -> bool:
    topic = topic or os.getenv("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC が未設定のため通知をスキップ")
        return False
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": priority,
                "Tags": tags,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"通知の送信に失敗: {exc}")
        return False
