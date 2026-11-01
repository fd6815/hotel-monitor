"""通知管道：LINE（Messaging API）、Telegram、ntfy，或只印在終端機。"""
import json
import os
from pathlib import Path

import requests


def _line_cfg(cfg):
    line = dict(cfg.get("line") or {})
    f = Path(cfg.get("data_dir", "data")) / "line.json"
    if f.exists():
        try:
            line = {**json.loads(f.read_text(encoding="utf-8")), **{k: v for k, v in line.items() if v}}
        except Exception:  # noqa: BLE001
            pass
    line["channel_token"] = line.get("channel_token") or os.environ.get("LINE_CHANNEL_TOKEN")
    line["user_id"] = line.get("user_id") or os.environ.get("LINE_USER_ID")
    return line


def send_line(cfg, text) -> bool:
    line = _line_cfg(cfg)
    if not line.get("channel_token"):
        return False
    msg = [{"type": "text", "text": text[:4900]}]
    try:
        uid = line.get("user_id") or ""
        if uid.startswith("U"):      # 指定個人
            url, body = "https://api.line.me/v2/bot/message/push", {"to": uid, "messages": msg}
        else:                        # 沒有 user ID：廣播給官方帳號所有好友（只有自己時等同私訊）
            url, body = "https://api.line.me/v2/bot/message/broadcast", {"messages": msg}
        r = requests.post(url, headers={"Authorization": f"Bearer {line['channel_token']}"}, json=body, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        body = getattr(getattr(e, "response", None), "text", "")
        print(f"[notify] LINE 失敗：{e} {body[:200]}")
        return False


def send(cfg: dict, text: str):
    sent = send_line(cfg, text)
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
