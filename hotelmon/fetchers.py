"""抓價：用 Playwright 開官網訂房頁，從頁面文字解析最低積分或現金價。

不靠 AI，純規則解析。各集團官網改版時，只需調整 TEMPLATES 或在 config
裡為單一飯店指定 url_template / price_selector。
"""
import asyncio
import hashlib
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass
class Quote:
    hotel: str
    stay_date: date
    price: int | None
    unit: str | None
    status: str          # ok / sold_out / error
    note: str = ""


# 各集團訂房頁網址（單晚、1 間房）。{變數} 由 build_url 填入。
TEMPLATES = {
    "marriott": (
        "https://www.marriott.com/reservation/availabilitySearch.mi?propertyCode={code}"
        "&fromDate={ci_mdy}&toDate={co_mdy}&numberOfRooms=1&numberOfAdults={adults}"
        "&useRewardsPoints=true"
    ),
    "ihg": (
        "https://www.ihg.com/hotels/us/en/find-hotels/select-roomrate?qSlH={code}"
        "&qRms=1&qAdlt={adults}&qChld=0&qCiD={ci_d}&qCiMy={ci_my_ihg}"
        "&qCoD={co_d}&qCoMy={co_my_ihg}&qRtP=IVANI&qAAR=IVANI&setPMCookies=true"
    ),
    "hyatt": (
        "https://www.hyatt.com/shop/rooms/{code}?checkinDate={ci}&checkoutDate={co}"
        "&rooms=1&adults={adults}&kids=0&rate=Standard&rateFilter=woh"
    ),
    "accor": (
        "https://all.accor.com/ssr/app/accor/rates/{code}/index.en.shtml"
        "?dateIn={ci}&nights=1&compositions={adults}&stayplus=false"
    ),
}

SOLD_OUT = re.compile(
    r"sold out|no rooms? (?:are )?available|no availability|not available for|"
    r"無空房|已售完|已客滿|满房|無可用房",
    re.I,
)
POINTS = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,7})\s*(?:points?|pts|點數|點|積分|积分)", re.I)
CASH = re.compile(
    r"(?:(TWD|NT\$|JPY|¥|USD|US\$|\$|THB|฿|KRW|₩|HKD|HK\$|SGD|S\$|EUR|€)\s?"
    r"(\d{1,3}(?:,\d{3})+|\d{2,7})(?:\.\d{1,2})?)"
)
CUR_NORMAL = {"NT$": "TWD", "¥": "JPY", "US$": "USD", "$": "USD", "฿": "THB",
              "₩": "KRW", "HK$": "HKD", "S$": "SGD", "€": "EUR"}


def build_url(hotel: dict, d: date, adults: int) -> str:
    tpl = hotel.get("url_template") or TEMPLATES[hotel["chain"]]
    co = d + timedelta(days=1)
    return tpl.format(
        code=hotel.get("code", ""), adults=adults,
        ci=d.isoformat(), co=co.isoformat(),
        ci_mdy=d.strftime("%m/%d/%Y"), co_mdy=co.strftime("%m/%d/%Y"),
        ci_d=f"{d.day:02d}", co_d=f"{co.day:02d}",
        # IHG 的月份是 0 起算（一月=00）
        ci_my_ihg=f"{d.month - 1:02d}{d.year}", co_my_ihg=f"{co.month - 1:02d}{co.year}",
    )


def parse_price(text: str, hotel: dict):
    """回傳 (price, unit)；找不到回傳 (None, None)。取頁面上的最低值。"""
    lo, hi = hotel.get("price_range", [1000, 300000] if hotel.get("mode", "points") == "points" else [500, 2000000])
    ignore = set(hotel.get("ignore_values", []))
    if hotel.get("mode", "points") == "points":
        vals = [int(m.group(1).replace(",", "")) for m in POINTS.finditer(text)]
        vals = [v for v in vals if lo <= v <= hi and v not in ignore]
        return (min(vals), "pts") if vals else (None, None)
    want = hotel.get("currency")
    found = []
    for m in CASH.finditer(text):
        cur = CUR_NORMAL.get(m.group(1), m.group(1))
        v = int(m.group(2).replace(",", ""))
        if (want is None or cur == want) and lo <= v <= hi and v not in ignore:
            found.append((v, cur))
    return min(found) if found else (None, None)


class BrowserFetcher:
    """以本機 Chrome 的獨立設定檔執行，第一次可手動登入會員，之後沿用登入狀態。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.b = cfg.get("browser", {})
        self.debug_dir = Path(cfg.get("data_dir", "data")) / "debug"

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        kw = dict(
            headless=self.b.get("headless", False),
            locale=self.b.get("locale", "en-US"),
            viewport={"width": 1280, "height": 900},
        )
        if self.b.get("channel"):
            kw["channel"] = self.b["channel"]
        prof = self.b.get("profile_dir", "data/browser-profile")
        try:
            self.ctx = await self._pw.chromium.launch_persistent_context(prof, **kw)
        except Exception as e:  # noqa: BLE001  沒裝 Chrome 時改用內建 Chromium
            if not kw.pop("channel", None):
                raise
            print(f"[browser] 無法啟動 Chrome，改用 Chromium：{str(e)[:80]}")
            self.ctx = await self._pw.chromium.launch_persistent_context(prof, **kw)
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        await self.ctx.close()
        await self._pw.stop()

    async def fetch(self, hotel: dict, d: date) -> Quote:
        url = build_url(hotel, d, self.cfg.get("adults", 2))
        timeout = int(self.b.get("page_timeout", 45))
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            sel = hotel.get("price_selector")
            polls = max(4, timeout // 2)
            for i in range(polls):
                await self.page.wait_for_timeout(2000)
                if sel:
                    els = await self.page.query_selector_all(sel)
                    text = "\n".join([await e.inner_text() for e in els])
                else:
                    text = await self.page.inner_text("body")
                price, unit = parse_price(text, hotel)
                if price:
                    return Quote(hotel["id"], d, price, unit, "ok")
                # 結果還在載入時可能短暫出現「not available」字樣，等幾輪再判定
                if i >= 3 and SOLD_OUT.search(text):
                    return Quote(hotel["id"], d, None, None, "sold_out")
            await self._debug_shot(hotel, d)
            return Quote(hotel["id"], d, None, None, "error", "頁面找不到價格")
        except Exception as e:  # noqa: BLE001
            await self._debug_shot(hotel, d)
            return Quote(hotel["id"], d, None, None, "error", str(e)[:200])

    async def _debug_shot(self, hotel, d):
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(self.debug_dir / f"{hotel['id']}_{d}.png"))
        except Exception:  # noqa: BLE001
            pass


class DemoFetcher:
    """模擬資料，用來測試通知與日曆頁，不連網。"""

    def __init__(self, fetch_day: date):
        self.fetch_day = fetch_day

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def fetch(self, hotel: dict, d: date) -> Quote:
        seed = int(hashlib.md5(f"{hotel['id']}{d}".encode()).hexdigest()[:8], 16)
        base_rnd = random.Random(seed)
        day_rnd = random.Random(seed + self.fetch_day.toordinal())
        if day_rnd.random() < 0.05:
            return Quote(hotel["id"], d, None, None, "sold_out")
        base = hotel.get("demo_base", 55000)
        step = 1000 if hotel.get("mode", "points") == "points" else 200
        weekend = step * 6 if d.weekday() in (4, 5) else 0
        p = base + weekend + base_rnd.randint(-5, 5) * step + day_rnd.choice([-2, -1, 0, 0, 0, 0, 1, 2]) * step
        unit = "pts" if hotel.get("mode", "points") == "points" else hotel.get("currency", "TWD")
        await asyncio.sleep(0)
        return Quote(hotel["id"], d, p, unit, "ok")
