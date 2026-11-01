"""通知管道：Telegram（建議）、ntfy、或只印在終端機。"""
import os

import requests


def send(cfg: dict, text: str):
    sent = False
    tg = dict(cfg.get("telegram") or {})
    tg["bot_token"] = tg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
    tg["chat_id"] = tg.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    if tg.get("bot_token") and tg.get("chat_id"):
        try:
            requests.post(
                f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
                json={"chat_id": tg["chat_id"], "text": text, "disable_web_page_preview": True},
                timeout=15,
            ).raise_for_status()
            sent = True
        except Exception as e:  # noqa: BLE001
            print(f"[notify] Telegram 失敗：{e}")
    ntfy = cfg.get("ntfy") or {}
    if ntfy.get("topic"):
        try:
            requests.post(f"{ntfy.get('server', 'https://ntfy.sh')}/{ntfy['topic']}",
                          data=text.encode(), timeout=15).raise_for_status()
            sent = True
        except Exception as e:  # noqa: BLE001
            print(f"[notify] ntfy 失敗：{e}")
    print("[notify]" + ("" if sent else "（未設定通知管道，僅顯示）") + "\n" + text + "\n")
