"""匯率：換算成台幣。每天更新一次，存在 data/fx.json；抓不到時用內建概略匯率。"""
import json
import time
from pathlib import Path

import requests

FALLBACK = {"TWD": 1, "JPY": 4.75, "USD": 0.031, "HKD": 0.24, "THB": 1.05, "KRW": 43, "SGD": 0.041,
            "EUR": 0.028, "CNY": 0.22, "MYR": 0.14, "VND": 790, "PHP": 1.8, "IDR": 505, "AUD": 0.047, "GBP": 0.024}


def rates(cfg) -> dict:
    """回傳 {幣別: 1 台幣可換多少該幣別}"""
    f = Path(cfg.get("data_dir", "data")) / "fx.json"
    try:
        if f.exists() and time.time() - f.stat().st_mtime < 86400:
            return json.loads(f.read_text(encoding="utf-8"))
        r = requests.get("https://open.er-api.com/v6/latest/TWD", timeout=15)
        r.raise_for_status()
        rt = r.json()["rates"]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rt), encoding="utf-8")
        return rt
    except Exception:  # noqa: BLE001
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return FALLBACK


def to_twd(amount, cur, rt):
    if amount is None or not cur:
        return None
    r = rt.get(cur.upper()) or FALLBACK.get(cur.upper())
    return round(amount / r) if r else None
