#!/usr/bin/env python3
"""飯店房價監控

  python run.py daily     抓所有飯店未來 N 天房價 → 通知 → 產生日曆頁（建議每天 1–2 次）
  python run.py watch     只抓 monitors 裡的日期 → 通知（建議每 30–60 分鐘）
  python run.py deals     只檢查優惠 RSS
  python run.py report    只重產日曆頁
  python run.py login     開瀏覽器讓你登入各集團會員（登入狀態會保留）

  共用參數：-c config.yaml   --fetch-day YYYY-MM-DD（測試用，模擬某天抓取）
"""
import argparse
import asyncio
import random
from datetime import date, timedelta

import sys

import yaml

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from hotelmon import alerts, deals, notify, report
from hotelmon.db import DB
from hotelmon.fetchers import BrowserFetcher, DemoFetcher


def load(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for h in cfg.get("hotels", []):
        h.setdefault("mode", "points")
    return cfg


def target_dates(cfg, hotel, mode):
    today = date.today()
    mon = {date.fromisoformat(str(m["date"])) for m in cfg.get("monitors", [])
           if m["hotel"] == hotel["id"] and str(m["date"]) >= today.isoformat()}
    if mode == "watch":
        return sorted(mon)
    n = hotel.get("days_ahead", cfg.get("days_ahead", 60))
    return sorted({today + timedelta(days=i) for i in range(n)} | mon)


async def collect(cfg, db, hotels, mode, fetch_day):
    results = {}
    lo, hi = cfg.get("browser", {}).get("delay_seconds", [4, 9])
    demo = [h for h in hotels if h["chain"] == "demo"]
    real = [h for h in hotels if h["chain"] != "demo"]
    groups = []
    if demo:
        groups.append((DemoFetcher(date.fromisoformat(fetch_day)), demo, 0))
    if real:
        groups.append((BrowserFetcher(cfg), real, 1))
    for fetcher, hs, delay in groups:
        async with fetcher as f:
            for h in hs:
                res = []
                dates = target_dates(cfg, h, mode)
                for i, d in enumerate(dates, 1):
                    q = await f.fetch(h, d)
                    db.save_quote(q, fetch_day)
                    res.append(q)
                    print(f"  {h['name']} {d} {q.status} {q.price or ''} {q.note}")
                    if delay and i < len(dates):
                        await asyncio.sleep(random.uniform(lo, hi))
                results[h["id"]] = res
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "watch", "deals", "report", "login"])
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--fetch-day", default=date.today().isoformat())
    a = ap.parse_args()

    cfg = load(a.config)
    db = DB(cfg.get("db_path", "data/hotelmon.db"))
    hotels = [h for h in cfg.get("hotels", []) if h.get("enabled", True)]
    by_id = {h["id"]: h for h in hotels}

    if a.cmd == "login":
        asyncio.run(login(cfg))
        return

    msgs = []
    if a.cmd in ("daily", "watch"):
        results = asyncio.run(collect(cfg, db, hotels, a.cmd, a.fetch_day))
        msgs += alerts.check_monitors(db, cfg, by_id)
        for h in hotels:
            msgs += alerts.check_errors(db, h, results.get(h["id"], []), a.fetch_day)
            if a.cmd == "daily":
                msgs += alerts.check_drops(db, cfg, h, a.fetch_day)
    if a.cmd in ("daily", "watch", "deals"):
        msgs += deals.check_deals(db, cfg)
    for m in msgs:
        notify.send(cfg, m)
    if a.cmd in ("daily", "watch", "report"):
        print("日曆頁：", report.write(db, cfg, hotels))


async def login(cfg):
    from playwright.async_api import async_playwright
    b = cfg.get("browser", {})
    async with async_playwright() as pw:
        kw = {"headless": False}
        if b.get("channel"):
            kw["channel"] = b["channel"]
        prof = b.get("profile_dir", "data/browser-profile")
        try:
            ctx = await pw.chromium.launch_persistent_context(prof, **kw)
        except Exception:  # noqa: BLE001
            kw.pop("channel", None)
            ctx = await pw.chromium.launch_persistent_context(prof, **kw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        for url in ["https://www.marriott.com/sign-in.mi", "https://www.ihg.com/rewardsclub/us/en/sign-in",
                    "https://www.hyatt.com/", "https://all.accor.com/"]:
            p = await ctx.new_page()
            await p.goto(url)
        await page.close()
        input("請在開啟的視窗登入各會員，完成後回到這裡按 Enter 關閉…")
        await ctx.close()


if __name__ == "__main__":
    main()
