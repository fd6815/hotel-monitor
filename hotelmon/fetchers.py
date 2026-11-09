"""抓價（純程式，不用 AI）

- 萬豪：用本機一般 Chrome（非自動化模式，透過 CDP 連線）開官網房價頁，解析「Points / Stay」。
  萬豪的防機器人系統會擋 Playwright 自己啟動的瀏覽器，所以改用一般 Chrome。
- IHG：在 ihg.com 頁面內直接呼叫官網 availability API，讀 rewardNights.pointsOnly.totalPoints；
  多天時改用月曆 API（一次一個月，只有點數）。
- 其他集團：開訂房頁，從頁面文字解析最低價格（需在 config 指定 url_template）。
"""
import asyncio
import hashlib
import json
import os
import random
import re
import subprocess
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
    cash: int | None = None        # 同日最低現金價（未稅）
    cash_unit: str | None = None   # 幣別，例如 JPY、TWD


IHG_API_KEY = "hsL8GAAz7drdIbEjBCejdA1Ud2MVhxoo"   # ihg.com 網頁前端公開使用的金鑰
IHG_CAL_KEY = "se9ym5iAzaW8pxfBjkmgbuGjJcr3Pj6Y"   # 月曆 API 用的金鑰（官網「view available dates」）

TEMPLATES = {
    # 會自動轉到 rateListMenu.mi，列出各房型「Points / Stay」
    "marriott": (
        "https://www.marriott.com/search/availabilityCalendar.mi?propertyCode={code}"
        "&fromDate={ci_mdy_enc}&toDate={co_mdy_enc}&flexibleDateSearch=false&lengthOfStay=1"
        "&childrenCount=0&clusterCode=none&useRewardsPoints={use_points}&isAdvanceSearch=true"
        "&isNGSF=false&numberOfRooms=1&numberOfAdults={adults}"
    ),
    "ihg": (
        "https://www.ihg.com/{ihg_path}/hoteldetail/rooms?qCiD={ci_day}&qCiMy={ci_my_ihg}"
        "&qCoD={co_day}&qCoMy={co_my_ihg}&qAdlt={adults}&qChld=0&qRms=1&qRtP={ihg_rate}"
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
    r"sold out|has no available rooms|no available rooms|no rooms? (?:are )?available|no availability|not available for|unavailable for your dates|"
    r"no rates (?:are )?available|無空房|已售完|已客滿|满房|無可用房",
    re.I,
)
MAR_CASH = re.compile(r"Rates from\s*([\d,]{3,})\s*([A-Z]{3})\s*/\s*Night", re.I)
MAR_CASH2 = re.compile(r"(?:¥|JPY|TWD|NT\$|USD|\$|THB|KRW|HKD|SGD|EUR|€)\s?([\d,]{3,})(?:\.\d+)?\s*(?:[A-Z]{3})?\s*/\s*Night", re.I)
MAR_POINTS = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,7})\s*Points\s*/\s*Stay", re.I)
POINTS = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,7})\s*(?:points?|pts|點數|點|積分|积分)", re.I)
CASH = re.compile(
    r"(?:(TWD|NT\$|JPY|¥|USD|US\$|\$|THB|฿|KRW|₩|HKD|HK\$|SGD|S\$|EUR|€)\s?"
    r"(\d{1,3}(?:,\d{3})+|\d{2,7})(?:\.\d{1,2})?)"
)
CUR_NORMAL = {"NT$": "TWD", "¥": "JPY", "US$": "USD", "$": "USD", "฿": "THB",
              "₩": "KRW", "HK$": "HKD", "S$": "SGD", "€": "EUR"}

CHROME_CANDIDATES = [
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def build_url(hotel: dict, d: date, adults: int, points: bool | None = None) -> str:
    tpl = hotel.get("url_template") or TEMPLATES[hotel["chain"]]
    co = d + timedelta(days=1)
    if points is None:
        points = hotel.get("mode", "points") == "points"
    return tpl.format(
        code=hotel.get("code", ""), adults=adults,
        ci=d.isoformat(), co=co.isoformat(),
        ci_mdy=d.strftime("%m/%d/%Y"), co_mdy=co.strftime("%m/%d/%Y"),
        ci_mdy_enc=d.strftime("%m%%2F%d%%2F%Y"), co_mdy_enc=co.strftime("%m%%2F%d%%2F%Y"),
        ci_day=d.day, co_day=co.day,
        # IHG 的月份是 0 起算（一月=00）
        ci_my_ihg=f"{d.month - 1:02d}{d.year}", co_my_ihg=f"{co.month - 1:02d}{co.year}",
        ihg_path=hotel.get("ihg_path", f"hotels/us/en/{hotel.get('code', '').lower()}"),
        ihg_rate="IVANI" if points else "IGCOR",
        use_points="true" if points else "false",
    )


def parse_price(text: str, hotel: dict):
    """通用解析：回傳 (price, unit)；找不到回傳 (None, None)。取頁面上的最低值。"""
    points = hotel.get("mode", "points") == "points"
    lo, hi = hotel.get("price_range", [1000, 500000] if points else [500, 5000000])
    ignore = set(hotel.get("ignore_values", []))
    if points:
        rx = MAR_POINTS if hotel["chain"] == "marriott" else POINTS
        vals = [int(m.group(1).replace(",", "")) for m in rx.finditer(text)]
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


def find_browser(cfg_path: str | None):
    if cfg_path and Path(cfg_path).exists():
        return cfg_path
    for c in CHROME_CANDIDATES:
        p = os.path.expandvars(c)
        if Path(p).exists():
            return p
    return None


IHG_JS = """async ([d1, d2, code, adults, key, rate]) => {
  const body = {products:[{productCode:"SR",guestCounts:[{otaCode:"AQC10",count:adults},{otaCode:"AQC8",count:0}],
      startDate:d1,endDate:d2,quantity:1}],
    startDate:d1,endDate:d2,hotelMnemonics:[code],rates:{ratePlanCodes:[{internal:rate}]},
    options:{disabilityMode:"ACCESSIBLE_AND_NON_ACCESSIBLE",returnAdditionalRatePlanDescriptions:false}};
  const r = await fetch("https://apis.ihg.com/availability/v3/hotels/offers?fieldset=rateDetails", {method:"POST",
    headers:{"content-type":"application/json","x-ihg-api-key":key,"accept-language":"en-US"},
    body:JSON.stringify(body), credentials:"include"});
  return {status:r.status, text: await r.text()};
}"""

IHG_CAL_JS = """async ([code, start, end, adults, key]) => {
  const body = {hotelMnemonics:[code], startDate:start, endDate:end, lengthOfStay:1,
    guestCounts:[{otaCode:"AQC10",count:adults}],
    options:{includeSellStrategy:"followChannel", returnAmountsAfterTaxForLowestOffer:false, returnAverages:true,
      lowestOfferPerRatePlan:true, identifyLowestOfferPerRatePlan:true},
    rates:{ratePlanCodes:["IVANI"]}};
  const r = await fetch("https://apis.ihg.com/availability/v1/calendar", {method:"POST",
    headers:{"content-type":"application/json; charset=UTF-8","accept":"application/json","x-ihg-api-key":key,"ihg-language":"en-US"},
    body:JSON.stringify(body), credentials:"include"});
  return {status:r.status, text: await r.text()};
}"""


MAR_CAL_QUERY = (Path(__file__).parent / "marriott_calendar.graphql").read_text(encoding="utf-8")
MAR_CAL_SIG = "887375892e1ad2a43f46a9c95c55ea47cf6eca3af03331c2134f1b440cff3f9f"
MAR_CAL_JS = """async ([code, start, end, adults, rtype, query, sig]) => {
  const body = {operationName:"phoenixShopADFSearchProductsByProperty",
    variables:{id:[code], search:{propertyId:code, options:{startDate:start, numberOfRooms:1,
      rateRequestTypes:[{type:rtype}], endDate:end, numberInParty:adults, numberOfDays:1}}}, query};
  const r = await fetch("/mi/query/phoenixShopADFSearchProductsByProperty", {method:"POST", credentials:"include",
    headers:{"content-type":"application/json","accept":"*/*","accept-language":"en-US",
      "apollographql-client-name":"phoenix_shop","apollographql-client-version":"v1","application-name":"shop",
      "graphql-operation-name":"phoenixShopADFSearchProductsByProperty","graphql-operation-signature":sig,
      "graphql-require-safelisting":"true"}, body:JSON.stringify(body)});
  return {status:r.status, text: await r.text()};
}"""


def parse_ihg(hotel: dict, d: date, status: int, text: str) -> Quote:
    hid = hotel["id"]
    if status == 400 and "los" in text.lower():
        return Quote(hid, d, None, None, "sold_out", "有最少住宿天數限制")
    if status != 200:
        return Quote(hid, d, None, None, "error", f"IHG API HTTP {status}: {text[:120]}")
    try:
        j = json.loads(text)
        h = j["hotels"][0]
        offers = h.get("rateDetails", {}).get("offers", [])
    except Exception as e:  # noqa: BLE001
        return Quote(hid, d, None, None, "error", f"IHG 回應格式異常：{e!s:.80}")
    if hotel.get("mode", "points") == "points":
        pts = []
        for o in offers:
            if o.get("ratePlanCode") != "IVANI" or o.get("availableStatus", "AVAILABLE") != "AVAILABLE":
                continue
            po = (o.get("rewardNights") or {}).get("pointsOnly") or {}
            v = po.get("totalPoints") or po.get("averageDailyPoints")
            if v:
                pts.append(int(v))
        cash, cur = _ihg_cash(h, offers, hotel)
        if pts:
            return Quote(hid, d, min(pts), "pts", "ok", cash=cash, cash_unit=cur)
        return Quote(hid, d, None, None, "sold_out", "無積分房", cash=cash, cash_unit=cur)
    cash, cur = _ihg_cash(h, offers, hotel)
    if cash:
        return Quote(hid, d, cash, cur, "ok", "未稅", cash=cash, cash_unit=cur)
    return Quote(hid, d, None, None, "sold_out")


def parse_ihg_calendar(text: str) -> dict:
    """月曆 API 回應 → {日期: 最低點數}；沒有積分房的日期不會出現。格式不對丟例外。"""
    j = json.loads(text)
    out = {}
    for h in j["data"]["hotels"]:
        for day in h.get("calendar") or []:
            pts = [int(o["checkInPoints"]) for o in day.get("offers") or []
                   if o.get("ratePlanCode") == "IVANI" and (o.get("checkInPoints") or 0) > 0]
            if day.get("start") and pts:
                out[day["start"]] = min(pts)
    return out


def _ihg_cash(h, offers, hotel):
    cur = h.get("propertyCurrency", hotel.get("currency", ""))
    vals = []
    for o in offers:
        if o.get("ratePlanCode") == "IVANI" or o.get("availableStatus", "AVAILABLE") != "AVAILABLE":
            continue
        try:
            vals.append(round(float(o["totalRate"]["amountBeforeTax"])))
        except Exception:  # noqa: BLE001
            pass
    return (min(vals), cur) if vals else (None, None)


class BrowserFetcher:
    """啟動本機一般 Chrome（獨立設定檔），以 CDP 連線控制；找不到 Chrome 時改用 Playwright 內建瀏覽器。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.b = cfg.get("browser", {})
        self.debug_dir = Path(cfg.get("data_dir", "data")) / "debug"
        self.proc = None
        self.ihg_page = None
        self.mar_page = None
        self.mar_cash_broken = False
        self.mar_blocked = False   # 這次執行已被萬豪擋下，不再對萬豪發任何請求

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        from . import vpn
        await asyncio.to_thread(vpn.warn_if_on)   # VPN 機房 IP 會被官網擋
        self._pw = await async_playwright().start()
        exe = find_browser(self.b.get("exe"))
        port = int(self.b.get("cdp_port", 9333))
        prof = Path(self.b.get("profile_dir", "data/chrome-profile")).resolve()
        if exe:
            args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                    "--no-first-run", "--no-default-browser-check", "--window-size=1280,900",
                    "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                    "--disable-background-timer-throttling",
                    "--disable-features=CalculateNativeWinOcclusion"]
            if self.b.get("offscreen", True):
                args.append("--window-position=-2400,0")
            args.append("about:blank")
            self.proc = subprocess.Popen(args)
            last = None
            for _ in range(20):
                await asyncio.sleep(1)
                try:
                    self.br = await self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
            else:
                raise RuntimeError(f"無法連線到 Chrome：{last}")
            self.ctx = self.br.contexts[0]
        else:
            print("[browser] 找不到 Chrome，改用 Playwright Chromium（萬豪可能會被擋）")
            self.br = None
            self.ctx = await self._pw.chromium.launch_persistent_context(
                str(prof), headless=False, viewport={"width": 1280, "height": 900})
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        try:
            if self.br:
                await self.br.close()
            else:
                await self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
        if self.proc:
            self.proc.terminate()
        await self._pw.stop()

    async def fetch(self, hotel: dict, d: date) -> Quote:
        try:
            if hotel["chain"] == "ihg" and not hotel.get("url_template"):
                return await self._ihg(hotel, d)
            return await self._page(hotel, d)
        except Exception as e:  # noqa: BLE001
            await self._debug(self.page, hotel, d)
            return Quote(hotel["id"], d, None, None, "error", str(e)[:200])

    async def marriott_range(self, hotel, dates):
        """用萬豪價格月曆 API 一次查最多 35 天（點數＋現金）。失敗回傳 None，改用逐日開網頁。"""
        if self.mar_page is None:
            self.mar_page = await self.ctx.new_page()
            await self.mar_page.goto("https://www.marriott.com/default.mi", wait_until="domcontentloaded", timeout=60000)
            await self.mar_page.wait_for_timeout(5000)
            if "access denied" in (await self.mar_page.title()).lower():
                self.mar_blocked = True
                return None
        want = sorted(set(dates))
        adults = int(self.cfg.get("adults", 2))
        points, cash = {}, {}
        i = 0
        while i < len(want):
            start = want[i]
            end = min(start + timedelta(days=34), want[-1])
            for rtype, store in (("REDEMPTION", points), ("STANDARD", cash)):
                if rtype == "STANDARD" and (hotel.get("cash") is False or self.mar_cash_broken):
                    continue
                res = await self.mar_page.evaluate(MAR_CAL_JS, [hotel["code"], start.isoformat(), end.isoformat(),
                                                                adults, rtype, MAR_CAL_QUERY, MAR_CAL_SIG])
                if "access denied" in res["text"].lower():
                    print(f"  [marriott] 月曆 API HTTP {res['status']}：Access Denied")
                    self.mar_blocked = True
                    return None
                if res["status"] != 200:
                    if rtype == "STANDARD":
                        self.mar_cash_broken = True
                        continue
                    print(f"  [marriott] 月曆 API HTTP {res['status']}：{res['text'][:120]}")
                    return None
                try:
                    j = json.loads(res["text"])
                    edges = j["data"]["search"]["calendarSearchByProperty"]["edges"]
                except Exception:  # noqa: BLE001
                    if rtype == "STANDARD":
                        self.mar_cash_broken = True
                        print(f"  [marriott] 現金月曆無法解析：{res['text'][:150]}")
                        continue
                    print(f"  [marriott] 月曆回應異常：{res['text'][:150]}")
                    return None
                for e in edges:
                    n = e["node"]
                    rm = n.get("rateModes") or {}
                    if rtype == "REDEMPTION":
                        pts = (rm.get("pointsPerQuantity") or {}).get("points")
                        if pts:
                            store[n["startDate"]] = int(pts)
                    else:
                        a = (rm.get("lowestAverageRate") or {}).get("amount") or {}
                        if a.get("amount") is not None:
                            store[n["startDate"]] = (round(a["amount"] / 10 ** int(a.get("decimalPoint") or 0)),
                                                     a.get("currency"))
                await asyncio.sleep(random.uniform(2, 4))
            i = next((k for k, d in enumerate(want) if d > end), len(want))
        out = []
        for d in want:
            k = d.isoformat()
            c, cu = cash.get(k, (None, None))
            if k in points:
                out.append(Quote(hotel["id"], d, points[k], "pts", "ok", cash=c, cash_unit=cu))
            else:
                out.append(Quote(hotel["id"], d, None, None, "sold_out", "無積分房", cash=c, cash_unit=cu))
        return out

    async def _ihg_open(self):
        if self.ihg_page is None:
            self.ihg_page = await self.ctx.new_page()
            await self.ihg_page.goto("https://www.ihg.com/hotels/us/en/reservation", wait_until="domcontentloaded", timeout=60000)
            await self.ihg_page.wait_for_timeout(5000)

    async def ihg_range(self, hotel, dates):
        """IHG 月曆 API：一次請求查最多 31 天的點數（現金由呼叫端沿用前次）。
        回傳 (quotes, 請求次數)；被擋回傳 error quotes（note 含 HTTP 403）；其他失敗回傳 (None, 次數) 改逐日查。"""
        await self._ihg_open()
        want = sorted(set(dates))
        points, calls, i = {}, 0, 0
        while i < len(want):
            start = want[i]
            end = min(start + timedelta(days=30), want[-1])
            res = await self.ihg_page.evaluate(IHG_CAL_JS, [hotel["code"], start.isoformat(), end.isoformat(),
                                                            int(self.cfg.get("adults", 2)), IHG_CAL_KEY])
            calls += 1
            if res["status"] in (403, 429) or "access denied" in res["text"].lower():
                note = f"IHG 月曆 API HTTP {res['status']}：Access Denied"
                return [Quote(hotel["id"], d, None, None, "error", note) for d in want], calls
            if res["status"] != 200:
                print(f"  [ihg] 月曆 API HTTP {res['status']}：{res['text'][:120]}")
                return None, calls
            try:
                points.update(parse_ihg_calendar(res["text"]))
            except Exception:  # noqa: BLE001
                print(f"  [ihg] 月曆回應異常：{res['text'][:150]}")
                return None, calls
            i = next((k for k, d in enumerate(want) if d > end), len(want))
            if i < len(want):
                await asyncio.sleep(random.uniform(2, 4))
        return [Quote(hotel["id"], d, points[d.isoformat()], "pts", "ok") if d.isoformat() in points
                else Quote(hotel["id"], d, None, None, "sold_out", "無積分房") for d in want], calls

    async def _ihg(self, hotel, d):
        await self._ihg_open()
        rate = "IVANI" if hotel.get("mode", "points") == "points" else "IGCOR"
        res = await self.ihg_page.evaluate(IHG_JS, [d.isoformat(), (d + timedelta(days=1)).isoformat(),
                                                    hotel["code"], int(self.cfg.get("adults", 2)),
                                                    hotel.get("api_key", IHG_API_KEY), rate])
        return parse_ihg(hotel, d, res["status"], res["text"])

    async def _page(self, hotel, d):
        q = await self._page_once(hotel, d)
        if hotel["chain"] == "marriott" and hotel.get("mode", "points") == "points" and hotel.get("cash", True) \
                and q.status != "error":
            await asyncio.sleep(random.uniform(5, 9))
            try:
                cash, cur = await self._marriott_cash(hotel, d)
                q.cash, q.cash_unit = cash, cur
            except Exception as e:  # noqa: BLE001
                print(f"  [cash] {hotel['name']} {d} 現金價失敗：{e!s:.80}")
        return q

    async def _marriott_cash(self, hotel, d):
        p = self.page
        await p.goto(build_url(hotel, d, self.cfg.get("adults", 2), points=False),
                     wait_until="domcontentloaded", timeout=45000)
        text = ""
        for i in range(15):
            await p.wait_for_timeout(2000)
            if "access denied" in (await p.title()).lower():
                return None, None
            text = await p.inner_text("body")
            vals = [(int(n.replace(",", "")), c.upper()) for n, c in MAR_CASH.findall(text)]
            if not vals:
                cur = "JPY" if ("¥" in text or "JPY" in text) else None
                vals = [(int(n.replace(",", "")), cur) for n in MAR_CASH2.findall(text)]
            vals = [v for v in vals if v[0] >= 100]
            if vals:
                return min(vals)
            if i >= 5 and SOLD_OUT.search(text):
                return None, None
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.debug_dir / f"cash_{hotel['id']}_{d}.txt").write_text(f"{p.url}\n\n{text[:3000]}", encoding="utf-8")
        return None, None

    async def _page_once(self, hotel, d):
        url = build_url(hotel, d, self.cfg.get("adults", 2))
        timeout = int(self.b.get("page_timeout", 45))
        p = self.page
        await p.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        sel = hotel.get("price_selector")
        for i in range(max(6, timeout // 2)):
            await p.wait_for_timeout(2000)
            title = await p.title()
            if "access denied" in title.lower():
                await self._debug(p, hotel, d)
                return Quote(hotel["id"], d, None, None, "error", "被官網防火牆擋下（Access Denied）")
            if sel:
                els = await p.query_selector_all(sel)
                text = "\n".join([await e.inner_text() for e in els])
            else:
                text = await p.inner_text("body")
            price, unit = parse_price(text, hotel)
            if price:
                cap = hotel.get("standard_max")
                if cap and price > cap:     # 標準房已滿、只剩高階房型
                    return Quote(hotel["id"], d, None, None, "sold_out", f"只剩高階房 {price:,}")
                return Quote(hotel["id"], d, price, unit, "ok")
            # 結果還在載入時可能短暫出現「not available」字樣，等幾輪再判定
            if i >= 4 and SOLD_OUT.search(text):
                return Quote(hotel["id"], d, None, None, "sold_out")
        await self._debug(p, hotel, d)
        return Quote(hotel["id"], d, None, None, "error", "頁面找不到價格")

    async def _debug(self, page, hotel, d):
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            t = (await page.inner_text("body"))[:1500]
            (self.debug_dir / f"{hotel['id']}_{d}.txt").write_text(f"{page.url}\n\n{t}", encoding="utf-8")
            await page.screenshot(path=str(self.debug_dir / f"{hotel['id']}_{d}.png"))
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
        cash = int((p * 0.55 + base_rnd.randint(-8, 8) * 600) / 100) * 100   # 模擬現金價（日圓）
        await asyncio.sleep(0)
        return Quote(hotel["id"], d, p, unit, "ok", cash=cash, cash_unit="JPY")
