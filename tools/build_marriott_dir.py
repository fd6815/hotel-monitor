"""從萬豪官網 sitemap 建立飯店清單 site/hotels-marriott.json：[[代碼, 名稱], ...]"""
import json
import re
import sys
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
SMALL = {"and", "of", "the", "at", "by", "on", "in", "a", "an", "de", "la", "le", "du", "des", "di", "da"}
UPPER = {"jw", "ac", "usa", "uk", "uae", "ii", "iii", "dc", "nyc", "lax", "sfo", "jfk"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read().decode("utf-8", "ignore")


def title(slug):
    out = []
    for i, w in enumerate(slug.split("-")):
        if w in UPPER:
            out.append(w.upper())
        elif i and w in SMALL:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def main():
    idx = get("https://www.marriott.com/sitemap-index.xml")
    maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", idx) if "/us-sitemap-hws-" in u]
    hotels = {}
    for u in maps:
        x = get(u)
        for code, slug in re.findall(r"/en-us/hotels/([a-z0-9]{5})-([a-z0-9-]+)/overview/", x):
            hotels[code.upper()] = title(slug)
        print(u, len(hotels), file=sys.stderr)
    if len(hotels) < 3000:
        sys.exit(f"清單太少（{len(hotels)}），可能被擋，不更新")
    out = Path("site/hotels-marriott.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(sorted(hotels.items()), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成 {len(hotels)} 間")


main()
