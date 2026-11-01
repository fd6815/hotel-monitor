"""通知規則：
1. 指定日期監控：價格低於門檻 → 通知一次並標記「已完成」
2. 收藏飯店降價／新開：與前一次抓取比較，降幅 ≥ drop_min 或由搶空變有房
3. 抓取失敗：某飯店當天全部失敗（多半是被擋或網頁改版）→ 通知一次
"""
from datetime import date, datetime, timedelta

from .fetchers import build_url

BLOCK_MINUTES = 30      # 被官網擋之後，最短隔多久再重試
BLOCK_MAX_MINUTES = 360 # 一直被擋就把間隔拉長（等於已被擋多久），最長 6 小時，避免重試延長封鎖


def hotel_failed(results) -> bool:
    """這次抓取算失敗：連 3 筆以上出錯（被官網擋、瀏覽器中途關閉），或全部日期都失敗；零星出錯不算。"""
    errors = sum(q.status == "error" for q in results or [])
    return errors > 0 and errors >= min(3, len(results))


def update_blocks(db, hotels, results):
    """記錄各集團是否被擋：有飯店失敗就暫停重試，全部成功就解除。"""
    failed = {}
    for h in hotels:
        if results.get(h["id"]):
            failed[h["chain"]] = failed.get(h["chain"], False) or hotel_failed(results[h["id"]])
    for chain, bad in failed.items():
        if bad:
            db.mark_sent("blocked:" + chain)
            if not db.was_sent("blocked-since:" + chain):
                db.mark_sent("blocked-since:" + chain)
        else:
            db.clear_sent("blocked:" + chain)
            db.clear_sent("blocked-since:" + chain)


def blocked_chains(db) -> dict:
    """{集團: 下次可重試時間}"""
    out = {}
    for k in db.recent_keys("blocked:"):
        last = datetime.strptime(db.was_sent(k), "%Y-%m-%d %H:%M")
        since = db.was_sent("blocked-since:" + k[8:])
        held = (last - datetime.strptime(since, "%Y-%m-%d %H:%M")).total_seconds() / 60 if since else 0
        out[k[8:]] = last + timedelta(minutes=min(BLOCK_MAX_MINUTES, max(BLOCK_MINUTES, held)))
    return out


def not_blocked(db, hotels) -> list:
    """濾掉還在封鎖等待中的集團，排程也不再去碰。"""
    wait = {c for c, t in blocked_chains(db).items() if t > datetime.now()}
    return [h for h in hotels if h["chain"] not in wait]


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
            # 萬豪資料來自第三方、可能過期：監控不結束，價格變了才再通知
            third_party = h["chain"] == "marriott" and not cfg.get("marriott_api")
            key = f"{monitor_key(m)}:{cur['price']}" if third_party else monitor_key(m)
            if db.was_sent(key):
                continue
            note = "\n資料來自 ustatz，可能不是最新，請到官網確認" if third_party else ""
            msgs.append(
                f"【監控達標】{h['name']} {d}\n目前 {fmt(cur['price'], cur['unit'])}（門檻 < {m['max_price']:,}）{note}\n{url}".strip()
            )
            db.mark_sent(key)
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
