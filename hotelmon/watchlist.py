"""監控清單：存在 GitHub repo 的 watch/watchlist.json，手機網頁可增減，電腦每次抓價前讀取。

格式：
{
  "hotels":   [{"id": "nagoya-marriott", "chain": "marriott", "code": "NGODT", "name": "名古屋萬豪",
                "city": "名古屋", "mode": "points", "alert_drop": true, "drop_min": 2000}],
  "monitors": [{"hotel": "nagoya-marriott", "date": "2026-11-28", "max_price": 60000},
               {"hotel": "ic-taipei", "date": "2026-12-01", "end": "2026-12-31", "max_price": 60000}]   # 區間監控
}
讀不到 GitHub 時沿用上次下載的 data/watchlist.json，再不行就用 config.yaml 的 hotels/monitors。
"""
import base64
import json
from datetime import date, timedelta
from pathlib import Path

import requests

from .publish import _token

DEFAULTS = {"mode": "points", "alert_drop": True, "drop_min": 2000, "enabled": True}
MAX_RANGE_DAYS = 92   # 區間監控最長幾天


def mon_dates(m: dict, today: date | None = None) -> list[date]:
    """監控涵蓋的日期：單日或 date～end（含），只留今天以後。"""
    start = date.fromisoformat(str(m["date"]))
    end = date.fromisoformat(str(m.get("end") or m["date"]))
    end = min(end, start + timedelta(days=MAX_RANGE_DAYS - 1))
    today = today or date.today()
    return [start + timedelta(days=i) for i in range((end - start).days + 1) if start + timedelta(days=i) >= today]


def mon_last(m: dict) -> str:
    return str(m.get("end") or m["date"])


def _normalize(w: dict) -> dict:
    hotels = []
    seen = set()
    for h in w.get("hotels", []):
        if not h.get("chain") or not h.get("code"):
            continue
        h = {**DEFAULTS, **h}
        h.setdefault("id", f"{h['chain']}-{h['code']}".lower())
        h.setdefault("name", h["code"])
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        hotels.append(h)
    ids = {h["id"] for h in hotels}
    monitors = []
    for m in w.get("monitors", []):
        try:
            if m["hotel"] in ids and m.get("date") and int(m.get("max_price", 0)) > 0:
                x = {"hotel": m["hotel"], "date": str(m["date"]), "max_price": int(m["max_price"])}
                if m.get("end") and str(m["end"]) > x["date"]:
                    x["end"] = str(m["end"])
                monitors.append(x)
        except (TypeError, ValueError):
            continue
    return {"hotels": hotels, "monitors": monitors}


def read_repo_json(cfg: dict, path: str):
    """讀 GitHub repo 裡的 JSON 檔；不存在回傳 None，連線失敗丟例外。"""
    gh = cfg.get("github") or {}
    h = {"Accept": "application/vnd.github+json"}
    t = _token(cfg)
    if t:
        h["Authorization"] = f"Bearer {t}"
    r = requests.get(f"https://api.github.com/repos/{gh['repo']}/contents/{path}",
                     headers=h, params={"ref": gh.get("branch", "main")}, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))


def load(cfg: dict) -> dict:
    gh = cfg.get("github") or {}
    cache = Path(cfg.get("data_dir", "data")) / "watchlist.json"
    repo = gh.get("repo")
    path = gh.get("watchlist_path", "watch/watchlist.json")
    if repo:
        try:
            h = {"Accept": "application/vnd.github+json"}
            t = _token(cfg)
            if t:
                h["Authorization"] = f"Bearer {t}"
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}",
                             headers=h, params={"ref": gh.get("branch", "main")}, timeout=20)
            if r.status_code == 200:
                w = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(w, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"[watchlist] 已讀取 GitHub 清單：{len(w.get('hotels', []))} 間飯店、{len(w.get('monitors', []))} 筆監控")
                return _normalize(w)
            print(f"[watchlist] GitHub 回應 {r.status_code}，改用本機快取")
        except Exception as e:  # noqa: BLE001
            print(f"[watchlist] 讀取失敗：{e}，改用本機快取")
    if cache.exists():
        return _normalize(json.loads(cache.read_text(encoding="utf-8")))
    return _normalize({"hotels": cfg.get("hotels", []), "monitors": cfg.get("monitors", [])})
