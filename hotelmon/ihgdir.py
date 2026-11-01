"""從 IHG 官網 sitemap 建立飯店清單 site/hotels-ihg.json：[[代碼, 品牌, 城市], ...]（品牌、城市是網址裡的 slug）。

ihg.com 會擋非瀏覽器連線，所以用 Chrome 在官網頁面內讀 sitemap。
"""
import re

from .fetchers import BrowserFetcher

SITEMAP_JS = """async () => {
  const locs = t => [...t.matchAll(/<loc>\\s*([^<\\s]+)/g)].map(m => m[1]);
  const index = await (await fetch("/bin/sitemapindex.xml")).text();
  const files = locs(index).filter(u => /\\.en-us\\.hoteldetail\\.xml$/.test(u));
  const urls = [];
  let failed = files.length ? 0 : 1;
  for (const f of files) {
    const r = await fetch(f);
    if (r.ok) urls.push(...locs(await r.text()).filter(u => u.endsWith("/hoteldetail")));
    else failed++;
    await new Promise(ok => setTimeout(ok, 300));
  }
  return {urls, failed};
}"""
URL = re.compile(r"ihg\.com/([a-z0-9-]+)/hotels/us/en/([a-z0-9-]+)/([a-z0-9]{5})/hoteldetail$")


def parse_rows(urls) -> list:
    rows = {}
    for u in urls:
        m = URL.search(u.lower())
        if m:
            rows.setdefault(m.group(3).upper(), [m.group(3).upper(), m.group(1), m.group(2)])
    return [rows[c] for c in sorted(rows)]


async def fetch(cfg) -> list:
    async with BrowserFetcher(cfg) as f:
        p = await f.ctx.new_page()
        await p.goto("https://www.ihg.com/robots.txt", wait_until="domcontentloaded", timeout=60000)
        res = await p.evaluate(SITEMAP_JS)
    if res["failed"]:
        raise RuntimeError(f"{res['failed']} 個 sitemap 讀取失敗")
    return parse_rows(res["urls"])
