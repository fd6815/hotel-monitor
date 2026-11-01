"""產生房價資料 dist/data.json（手機網頁讀取）與本機用的 dist/index.html（資料內嵌）。"""
import json
import statistics
from datetime import date
from pathlib import Path

from .alerts import blocked_chains, monitor_key
from .db import now
from .fetchers import build_url
from . import fx

CHAIN_LABEL = {"marriott": "萬豪", "ihg": "IHG", "hyatt": "凱悅", "accor": "雅高", "demo": "示範"}


def build(db, cfg, hotels):
    today = date.today().isoformat()
    rt = fx.rates(cfg)
    data = {"updated": now(), "hotels": [], "monitors": [], "deals": [], "fx": rt, "adults": cfg.get("adults", 2)}
    for h in hotels:
        days = {}
        all_snaps = db.snapshots(h["id"], today)
        oks = [v[0]["price"] for v in all_snaps.values() if v[0]["status"] == "ok" and v[0]["price"]]
        # 超過中位數 1.7 倍視為只剩高階房（標準房搶空）
        cap = h.get("standard_max") or (statistics.median(oks) * 1.7 if len(oks) >= 5 else None)
        for d, snaps in all_snaps.items():
            cur = snaps[0]
            prev = snaps[1] if len(snaps) > 1 else None
            delta, is_new = None, False
            if prev and cur["status"] == "ok":
                if prev["status"] == "ok":
                    delta = cur["price"] - prev["price"]
                elif prev["status"] == "sold_out":
                    is_new = True
            status = cur["status"]
            if status == "ok" and cap and cur["price"] > cap:
                status = "premium"
                delta, is_new = None, False
            cash = cur["cash"] if "cash" in cur.keys() else None
            cu = cur["cash_unit"] if "cash_unit" in cur.keys() else None
            twd = fx.to_twd(cash, cu, rt)
            val = round(twd / cur["price"] * 10000) if twd and status == "ok" and cur["price"] else None
            days[d] = {
                "p": cur["price"], "s": status, "u": cur["unit"], "n": cur["note"] or "",
                "c": twd, "cn": cash, "cu": cu, "v": val,
                "delta": delta, "new": is_new,
                "url": build_url(h, date.fromisoformat(d), cfg.get("adults", 2)) if h["chain"] != "demo" else "",
                "h": [[r["fetch_day"], r["price"], r["status"]] for r in reversed(snaps[:30])],
            }
        vals = sorted(x["v"] for x in days.values() if x.get("v"))
        good = h.get("value_good") or (vals[int(len(vals) * 0.75)] if len(vals) >= 8 else None)
        for x in days.values():
            x["g"] = bool(good and x.get("v") and x["v"] >= good)
        data["hotels"].append({
            "id": h["id"], "name": h["name"], "chain": CHAIN_LABEL.get(h["chain"], h["chain"]),
            "chain_id": h["chain"], "code": h.get("code", ""),
            "city": h.get("city", ""), "mode": h.get("mode", "points"),
            "standard_max": h.get("standard_max"), "value_good_cfg": h.get("value_good"),
            "last": db.last_fetch(h["id"]), "days": days,
            "value_range": [vals[0], vals[-1]] if vals else None, "value_good": good,
        })
    by_id = {h["id"]: h for h in hotels}
    for m in sorted(cfg.get("monitors", []), key=lambda x: str(x["date"])):
        d = str(m["date"])
        h = by_id.get(m["hotel"], {"name": m["hotel"]})
        snaps = db.snapshots(m["hotel"], d).get(d) or []
        cur = snaps[0] if snaps else None
        done = db.was_sent(monitor_key(m))
        data["monitors"].append({
            "date": d, "hotel": m["hotel"], "name": h["name"], "max": m["max_price"],
            "cur": cur["price"] if cur else None, "s": cur["status"] if cur else None,
            "unit": cur["unit"] if cur else None, "key": monitor_key(m),
            "state": "done" if done else ("expired" if d < today else "watch"), "done_at": done,
        })
    data["requests_done"] = [k[4:] for k in db.recent_keys("req:")]
    data["blocked"] = [{"chain": CHAIN_LABEL.get(c, c), "retry": t.strftime("%H:%M")} for c, t in blocked_chains(db).items()]
    for r in db.recent_deals(30):
        data["deals"].append({"title": r["title"], "link": r["link"], "source": r["source"], "seen": r["seen_at"]})
    return data


def write(db, cfg, hotels):
    out = Path(cfg.get("output_dir", "dist"))
    out.mkdir(parents=True, exist_ok=True)
    data = build(db, cfg, hotels)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tpl = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (out / "index.html").write_text(tpl.replace("null/*__DATA__*/", payload), encoding="utf-8")
    return out / "data.json"
