"""通知規則：
1. 指定日期監控：價格低於門檻 → 通知一次並標記「已完成」
2. 收藏飯店降價／新開：與前一次抓取比較，降幅 ≥ drop_min 或由搶空變有房
3. 抓取失敗：某飯店當天全部失敗（多半是被擋或網頁改版）→ 通知一次
"""
from datetime import date

from .fetchers import build_url


def fmt(price, unit):
    if price is None:
        return "-"
    return f"{price:,} 點" if unit == "pts" else f"{unit} {price:,}"


def monitor_key(m):
    return f"mon:{m['hotel']}:{m['date']}:{m['max_price']}"


def check_monitors(db, cfg, hotels_by_id) -> list[str]:
    msgs = []
    today = date.today().isoformat()
    for m in cfg.get("monitors", []):
        d = str(m["date"])
        if d < today or db.was_sent(monitor_key(m)):
            continue
        h = hotels_by_id.get(m["hotel"])
        if not h:
            continue
        snaps = db.snapshots(h["id"], d).get(d)
        if not snaps:
            continue
        cur = snaps[0]
        if cur["status"] == "ok" and cur["price"] is not None and cur["price"] < m["max_price"]:
            url = build_url(h, date.fromisoformat(d), cfg.get("adults", 2)) if h["chain"] != "demo" else ""
            msgs.append(
                f"【監控達標】{h['name']} {d}\n目前 {fmt(cur['price'], cur['unit'])}（門檻 < {m['max_price']:,}）\n{url}".strip()
            )
            db.mark_sent(monitor_key(m))
    return msgs


def check_drops(db, cfg, hotel, fetch_day: str) -> list[str]:
    if not hotel.get("alert_drop"):
        return []
    drop_min = hotel.get("drop_min", 2000)
    lines = []
    for d, snaps in db.snapshots(hotel["id"], date.today().isoformat()).items():
        if snaps[0]["fetch_day"] != fetch_day or len(snaps) < 2:
            continue
        cur, prev = snaps[0], snaps[1]
        key = None
        if cur["status"] == "ok" and prev["status"] == "sold_out":
            key, line = f"new:{hotel['id']}:{d}:{fetch_day}", f"{d} 新開 {fmt(cur['price'], cur['unit'])}"
        elif cur["status"] == "ok" and prev["status"] == "ok" and prev["price"] - cur["price"] >= drop_min:
            key = f"drop:{hotel['id']}:{d}:{cur['price']}:{fetch_day}"
            line = f"{d} {fmt(cur['price'], cur['unit'])}（降 {prev['price'] - cur['price']:,}）"
        if key and not db.was_sent(key):
            db.mark_sent(key)
            lines.append(line)
    if not lines:
        return []
    return [f"【降價】{hotel['name']}\n" + "\n".join(lines[:20]) + (f"\n…共 {len(lines)} 天" if len(lines) > 20 else "")]


def check_errors(db, hotel, results, fetch_day) -> list[str]:
    if results and all(q.status == "error" for q in results):
        key = f"err:{hotel['id']}:{fetch_day}"
        if not db.was_sent(key):
            db.mark_sent(key)
            return [f"【抓取失敗】{hotel['name']} 全部日期都失敗：{results[0].note}\n請看 data/debug 截圖，可能需重新登入或調整網址"]
    return []
