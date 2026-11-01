"""優惠情報：讀 RSS，標題或摘要含關鍵字的新文章就通知。首次執行只建立基準、不通知。"""
import re
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "Mozilla/5.0 (hotel-monitor RSS reader)"}


def _items(xml_text: str):
    root = ET.fromstring(xml_text)
    # RSS 2.0
    for it in root.iter("item"):
        yield (it.findtext("title") or "", it.findtext("link") or "",
               it.findtext("description") or "", it.findtext("pubDate") or "")
    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for e in root.iter(f"{ns}entry"):
        link = e.find(f"{ns}link")
        yield (e.findtext(f"{ns}title") or "", link.get("href") if link is not None else "",
               e.findtext(f"{ns}summary") or "", e.findtext(f"{ns}updated") or "")


def check_deals(db, cfg) -> list[str]:
    dc = cfg.get("deals") or {}
    feeds = dc.get("feeds", [])
    if not feeds:
        return []
    kw = [k.lower() for k in dc.get("keywords", [])]
    exclude = [k.lower() for k in dc.get("exclude", [])]
    bootstrap = db.deals_count() == 0
    new = []
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            items = list(_items(r.text))
        except Exception as e:  # noqa: BLE001
            print(f"[deals] {url} 讀取失敗：{e}")
            continue
        src = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        for title, link, desc, pub in items[:40]:
            if not link or db.deal_seen(link):
                continue
            hay = f"{title} {desc}".lower()
            matched = (not kw or any(k in hay for k in kw)) and not any(x in hay for x in exclude)
            db.add_deal(link, title.strip(), src, pub, matched)
            if matched and not bootstrap:
                new.append(f"- {title.strip()}\n  {link}")
    if not new:
        return []
    return ["【優惠情報】\n" + "\n".join(new[:10])]
