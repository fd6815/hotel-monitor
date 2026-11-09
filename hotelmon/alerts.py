"""通知規則：
1. 指定日期監控：價格低於門檻 → 通知一次並標記「已完成」
2. 收藏飯店降價／新開：與前一次抓取比較，降幅 ≥ drop_min 或由搶空變有房
3. 抓取失敗：某飯店當天全部失敗（多半是被擋或網頁改版）→ 通知一次
4. 一年內新低：收藏飯店未來一年最低點數創新低 → 通知一次並記為新紀錄（第一次只記錄不通知）
"""
from datetime import date, datetime, timedelta

from .fetchers import build_url

BLOCK_MINUTES = 30      # 被官網擋之後，最短隔多久再重試
BLOCK_MAX_MINUTES = 360 # 一直被擋就把間隔拉長（等於已被擋多久），最長 6 小時，避免重試延長封鎖
OFFICIAL = "官網"        # prices.note：這筆萬豪價格來自官網，不是 ustatz


def is_denied(note) -> bool:
    """官網防火牆擋下（萬豪、IHG 都是 Akamai 的 Access Denied；403／429 也算）。"""
    return any(s in (note or "") for s in ("Access Denied", "HTTP 403", "HTTP 429"))


def hotel_failed(results) -> bool:
    """這次抓取算失敗：被官網擋過一次、連 3 筆以上出錯（瀏覽器中途關閉），或全部日期都失敗；零星出錯不算。"""
    if any(is_denied(q.note) for q in results or []):
        return True
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
    end = f"~{m['end']}" if m.get("end") else ""
    return f"mon:{m['hotel']}:{m['date']}{end}:{m['max_price']}"


def mon_rows(db, m, today=None) -> dict:
    """監控涵蓋日期（今天以後）各自最新一筆資料 {日期: row}。"""
    from .watchlist import mon_dates
    out = {}
    for d in mon_dates(m, today):
        snaps = db.snapshots(m["hotel"], d.isoformat()).get(d.isoformat())
        if snaps:
            out[d.isoformat()] = snaps[0]
    return out


def _third_party(cfg, h, row) -> bool:
    """萬豪資料來自 ustatz、可能過期：監控不結束，價格變了才再通知。"""
    return h["chain"] == "marriott" and not cfg.get("marriott_api") and row["note"] != OFFICIAL


def _cash(row) -> str:
    """通知裡附上同日現金價（萬豪含稅、IHG 未稅）。"""
    c = row["cash"] if "cash" in row.keys() else None
    return f"／現金 {row['cash_unit'] or ''} {c:,}" if c else ""


def check_monitors(db, cfg, hotels_by_id) -> list[str]:
    """單日：低於門檻通知一次並完成。區間：每個低於門檻的日期各通知一次，區間結束前持續監控。"""
    msgs = []
    for m in cfg.get("monitors", []):
        h = hotels_by_id.get(m["hotel"])
        if not h or db.was_sent(monitor_key(m)):
            continue
        hits = []
        for d, cur in mon_rows(db, m).items():
            if cur["status"] != "ok" or cur["price"] is None or cur["price"] >= m["max_price"]:
                continue
            third = _third_party(cfg, h, cur)
            key = monitor_key(m) + (f":{d}" if m.get("end") else "") + (f":{cur['price']}" if third else "")
            if not db.was_sent(key):
                db.mark_sent(key)
                hits.append((d, cur, third))
        if not hits:
            continue
        url = build_url(h, date.fromisoformat(hits[0][0]), cfg.get("adults", 2)) if h["chain"] != "demo" else ""
        note = "\n資料來自 ustatz，可能不是最新，請到官網確認" if any(x[2] for x in hits) else ""
        if m.get("end"):
            lines = "\n".join(f"{d} {fmt(c['price'], c['unit'])}{_cash(c)}" for d, c, _ in hits[:15])
            more = f"\n…共 {len(hits)} 天" if len(hits) > 15 else ""
            msgs.append(f"【區間監控達標】{h['name']} {m['date']}～{m['end']}（門檻 < {m['max_price']:,}）\n"
                        f"{lines}{more}{note}\n{url}".strip())
        else:
            d, cur, third = hits[0]
            if not third:
                db.mark_sent(monitor_key(m))
            msgs.append(f"【監控達標】{h['name']} {d}\n目前 {fmt(cur['price'], cur['unit'])}{_cash(cur)}"
                        f"（門檻 < {m['max_price']:,}）{note}\n{url}".strip())
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


LOW_COVER_DAYS = 300   # 資料要涵蓋到這麼多天後，才算「一年內」（IHG 一年資料還沒抓到前不記錄）


def check_year_low(db, cfg, hotel, today=None) -> list[str]:
    """未來一年最低點數比紀錄（counters 表 low:<飯店>）還低 → 通知一次並更新紀錄；第一次只記錄。"""
    if hotel.get("mode", "points") != "points" or hotel["chain"] == "demo":
        return []
    today = today or date.today()
    end = (today + timedelta(days=365)).isoformat()
    snaps = {d: v[0] for d, v in db.snapshots(hotel["id"], today.isoformat()).items() if d < end}
    if not snaps or max(snaps) < (today + timedelta(days=LOW_COVER_DAYS)).isoformat():
        return []
    oks = {d: r for d, r in snaps.items() if r["status"] == "ok" and r["unit"] == "pts" and r["price"]}
    if not oks:
        return []
    low = min(r["price"] for r in oks.values())
    key = f"low:{hotel['id']}"
    rec = db.counter(key)
    if rec and low >= rec:
        return []
    db.set_counter(key, low)
    if not rec:
        print(f"  [新低] {hotel['name']} 一年內最低 {low:,} 點，記錄為基準")
        return []
    days = sorted(d for d, r in oks.items() if r["price"] == low)
    third = hotel["chain"] == "marriott" and not cfg.get("marriott_api") and any(oks[d]["note"] != OFFICIAL for d in days)
    note = "\n資料來自 ustatz，可能不是最新，請到官網確認" if third else ""
    url = build_url(hotel, date.fromisoformat(days[0]), cfg.get("adults", 2))
    return [f"【一年內新低】{hotel['name']}\n{fmt(low, 'pts')}（之前最低 {rec:,} 點）\n日期：{'、'.join(days[:8])}"
            f"{' 等' if len(days) > 8 else ''}{note}\n{url}"]


def check_errors(db, hotel, results, fetch_day) -> list[str]:
    if results and all(q.status == "error" for q in results):
        key = f"err:{hotel['id']}:{fetch_day}"
        if not db.was_sent(key):
            db.mark_sent(key)
            return [f"【抓取失敗】{hotel['name']} 全部日期都失敗：{results[0].note}\n請看 data/debug 截圖，可能需重新登入或調整網址"]
    return []
