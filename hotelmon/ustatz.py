"""萬豪資料來源：ustatz.com（PY的旅行大小事）的飯店頁，HTML 內嵌約 11 個月的點數與現金（含稅）。
資料是對方預先抓好的，不一定是最新，每間飯店每天只讀一次。
"""
import re
from datetime import date

import requests

from .fetchers import Quote

URL = "https://www.ustatz.com/hotelGroups/Taiwan/marriott"   # 路徑裡的國家不影響結果
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
FIELD = r'{}:\s*"([^"]*)"'


def parse(text: str, hotel: dict) -> list:
    today = date.today().isoformat()
    out = []
    for block in text.split("eachObj={")[1:]:
        field = lambda k: (re.search(FIELD.format(k), block) or [None, ""])[1]
        d, points = field("checkin"), field("awardPoints")
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", d) or d < today:
            continue
        try:
            cash = round(float(field("cash")))
        except ValueError:
            cash = None
        cur = field("currency") if cash is not None else None
        day = date.fromisoformat(d)
        if points.isdigit() and int(points) > 0:
            out.append(Quote(hotel["id"], day, int(points), "pts", "ok", cash=cash, cash_unit=cur))
        else:
            out.append(Quote(hotel["id"], day, None, None, "sold_out", "無積分房", cash=cash, cash_unit=cur))
    return out


def fetch(hotel: dict) -> list:
    r = requests.get(URL, headers=UA, timeout=30, params={"hotelcode": hotel["code"], "hotelname": hotel.get("name", "")})
    r.raise_for_status()
    rows = parse(r.text, hotel)
    if len(rows) < 30:   # 對方改版或查無此飯店，不要蓋掉舊資料
        raise ValueError(f"只解析到 {len(rows)} 天")
    return rows
