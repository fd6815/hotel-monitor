"""優惠情報：讀 RSS，用關鍵字規則篩選（不用 AI）。
網頁只留特價與優惠技巧；LINE 只通知 IHG／Avios／Alaska 點數特賣、bug 價、快速會籍匹配，並附中文翻譯。
每個來源第一次讀取只建立基準、不通知。
"""
import html
import re
import time
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "Mozilla/5.0 (hotel-monitor RSS reader)"}

KEEP = re.compile(
    r"\bsale\b|% ?(off|bonus)|\bdiscount|\bpromo|\bdeals?\b|\bflash\b|\bdouble\b|\btriple\b|\b\dx\b|\bbonus\b|\bfree night"
    r"|mistake fare|error fare|error rate|pricing error|\bglitch\b|\bbug\b|status match|status challenge|fast track"
    r"|\bhow to\b|\bmaximi[sz]e|\btips?\b|\btricks?\b|\bsweet spot|\bworth it\b|\bloophole"
    r"|特價|特賣|優惠|折扣|促銷|加碼|加贈|雙倍|三倍|回饋|買點|買里程|買哩程|錯價|BUG|會籍匹配|快速升級|攻略|技巧|教學|秘訣|甜蜜點|懶人包",
    re.I)
DROP = re.compile(
    r"\breview\b|\brant\b|compensation clinic|\bnews\b|\bbreach\b|\blawsuit\b|\bhack affect|\bbecomes\b|\bcredit card\b.*\bshould\b"
    r"|trip report|\bopens?\b|\bopening\b|\bclosing\b|開幕|開箱|住宿心得|飛行紀錄",
    re.I)
PROGRAM = re.compile(r"\bihg\b|\bavios\b|\balaska\b|\batmos\b|\bmileage plan\b|洲際|阿拉斯加", re.I)
POINT_SALE = re.compile(r"\bsale\b|\bbuy\b|\bpurchase|% ?off|特賣|買點|買里程|買哩程|促銷", re.I)
BUG = re.compile(r"mistake fare|error fare|error rate|pricing error|\bglitch\b|\bbug\b|錯價", re.I)
STATUS_MATCH = re.compile(r"status match|status challenge|fast track|會籍匹配|快速升級", re.I)
CJK = re.compile(r"[一-鿿]")


def is_useful(title: str) -> bool:
    """網頁要留的文章：特價或優惠技巧。"""
    return bool(KEEP.search(title)) and not DROP.search(title)


def is_alert(title: str) -> bool:
    """要傳 LINE 的文章。"""
    if DROP.search(title):
        return False
    return bool(BUG.search(title) or STATUS_MATCH.search(title)
                or (PROGRAM.search(title) and POINT_SALE.search(title)))


def _clean(text: str, n: int = 200) -> str:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def translate(text: str):
    """翻成繁體中文（Google 翻譯免費端點，不耗 Claude token）；已是中文或失敗就回傳原文。"""
    if not text or CJK.search(text):
        return text
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single", timeout=15,
                         params={"client": "gtx", "sl": "auto", "tl": "zh-TW", "dt": "t", "q": text})
        r.raise_for_status()
        return "".join(p[0] for p in r.json()[0] if p and p[0]) or text
    except Exception as e:  # noqa: BLE001
        print(f"[deals] 翻譯失敗：{e!s:.100}")
        return text


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


def reclassify(db):
    """規則改版時重新篩選舊文章（只做一次）。"""
    if db.was_sent("deals-v2"):
        return
    for r in db.all_deals():
        keep = is_useful(r["title"])
        db.update_deal(r["link"], keep, translate(r["title"]) if keep else None, None)
        if keep:
            time.sleep(0.3)
    db.mark_sent("deals-v2")


def check_deals(db, cfg) -> list[str]:
    feeds = (cfg.get("deals") or {}).get("feeds", [])
    if not feeds:
        return []
    reclassify(db)
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
        first = not db.was_sent("deal-src:" + url)
        db.mark_sent("deal-src:" + url)
        for title, link, desc, pub in items[:40]:
            title = title.strip()
            if not link or db.deal_seen(link):
                continue
            keep = is_useful(title)
            title_zh = summary_zh = None
            if keep:
                title_zh, summary_zh = translate(title), translate(_clean(desc))
                time.sleep(0.3)
            db.add_deal(link, title, src, pub, keep, title_zh, summary_zh)
            if not first and is_alert(title):
                new.append(f"- {title_zh or translate(title)}\n  {summary_zh or translate(_clean(desc))}\n  {link}")
    if not new:
        return []
    return ["【優惠情報】\n" + "\n".join(new[:10])]
