"""產生靜態日曆頁 dist/index.html（資料內嵌，雙擊即可開，也可丟 GitHub Pages）。"""
import json
from datetime import date
from pathlib import Path

from .alerts import monitor_key
from .db import now
from .fetchers import build_url

CHAIN_LABEL = {"marriott": "萬豪", "ihg": "IHG", "hyatt": "凱悅", "accor": "雅高", "demo": "示範"}


def build(db, cfg, hotels):
    today = date.today().isoformat()
    data = {"updated": now(), "hotels": [], "monitors": [], "deals": []}
    for h in hotels:
        days = {}
        for d, snaps in db.snapshots(h["id"], today).items():
            cur = snaps[0]
            prev = snaps[1] if len(snaps) > 1 else None
            delta, is_new = None, False
            if prev and cur["status"] == "ok":
                if prev["status"] == "ok":
                    delta = cur["price"] - prev["price"]
                elif prev["status"] == "sold_out":
                    is_new = True
            days[d] = {
                "p": cur["price"], "s": cur["status"], "u": cur["unit"],
                "delta": delta, "new": is_new,
                "url": build_url(h, date.fromisoformat(d), cfg.get("adults", 2)) if h["chain"] != "demo" else "",
                "h": [[r["fetch_day"], r["price"], r["status"]] for r in reversed(snaps[:30])],
            }
        data["hotels"].append({
            "id": h["id"], "name": h["name"], "chain": CHAIN_LABEL.get(h["chain"], h["chain"]),
            "city": h.get("city", ""), "mode": h.get("mode", "points"),
            "last": db.last_fetch(h["id"]), "days": days,
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
            "unit": cur["unit"] if cur else None,
            "state": "done" if done else ("expired" if d < today else "watch"), "done_at": done,
        })
    for r in db.recent_deals(30):
        data["deals"].append({"title": r["title"], "link": r["link"], "source": r["source"], "seen": r["seen_at"]})
    return data


def write(db, cfg, hotels):
    out = Path(cfg.get("output_dir", "dist"))
    out.mkdir(parents=True, exist_ok=True)
    tpl = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(build(db, cfg, hotels), ensure_ascii=False).replace("</", "<\\/")
    (out / "index.html").write_text(tpl.replace("__DATA__", payload), encoding="utf-8")
    return out / "index.html"
