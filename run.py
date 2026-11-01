#!/usr/bin/env python3
"""飯店房價監控

  python run.py daily     抓所有飯店未來 N 天房價 → 通知 → 產生日曆頁（建議每天 1–2 次）
  python run.py watch     只抓 monitors 裡的日期 → 通知（建議每 30–60 分鐘）
  python run.py deals     只檢查優惠 RSS
  python run.py report    只重產日曆頁
  python run.py login     開瀏覽器讓你登入各集團會員（登入狀態會保留）
  python run.py poll      檢查手機網頁送來的更新請求（每分鐘由排程執行，沒請求就立即結束）

  共用參數：-c config.yaml   --fetch-day YYYY-MM-DD（測試用，模擬某天抓取）
"""
import argparse
import asyncio
import calendar
import os
import random
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import sys

import yaml

BASE = Path(__file__).resolve().parent
os.chdir(BASE)   # 排程執行時工作目錄不一定在這裡
if sys.stdout is None or sys.stderr is None:   # pythonw 沒有主控台，輸出寫到 log
    (BASE / "data").mkdir(exist_ok=True)
    _log = open(BASE / "data" / "poll.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from hotelmon import alerts, deals, notify, publish, report, watchlist
from hotelmon.db import DB
from hotelmon.fetchers import BrowserFetcher, DemoFetcher


def load(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for h in cfg.get("hotels", []):
        h.setdefault("mode", "points")
    return cfg


def target_dates(cfg, hotel, mode, db=None):
    today = date.today()
    mon = {date.fromisoformat(str(m["date"])) for m in cfg.get("monitors", [])
           if m["hotel"] == hotel["id"] and str(m["date"]) >= today.isoformat()}
    # 新加入、還沒有資料的飯店：watch 也抓完整日曆
    if mode == "watch" and not (db and db.last_fetch(hotel["id"])):
        mode = "daily"
    if mode == "watch":
        return sorted(mon)
    n = hotel.get("days_ahead", cfg.get("days_ahead", 60))
    return sorted({today + timedelta(days=i) for i in range(n)} | mon)


async def collect(cfg, db, hotels, mode, fetch_day, targets=None):
    results = {}
    if targets is not None:
        hotels = [h for h in hotels if targets.get(h["id"])]
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
                dates = sorted(targets[h["id"]]) if targets is not None else target_dates(cfg, h, mode, db)
                # 萬豪：先用價格月曆 API 一次查整段，失敗才逐日開網頁
                if h["chain"] == "marriott" and hasattr(f, "marriott_range") and h.get("mode", "points") == "points":
                    try:
                        qs = await f.marriott_range(h, dates)
                    except Exception as e:  # noqa: BLE001
                        print(f"  [marriott] 月曆 API 失敗：{e!s:.120}")
                        qs = None
                    if qs is not None:
                        for q in qs:
                            db.save_quote(q, fetch_day)
                            print(f"  {h['name']} {q.stay_date} {q.status} {q.price or ''} {q.note} {q.cash or ''}{q.cash_unit or ''}")
                        results[h["id"]] = qs
                        continue
                denied = 0
                for i, d in enumerate(dates, 1):
                    q = await f.fetch(h, d)
                    res.append(q)
                    print(f"  {h['name']} {d} {q.status} {q.price or ''} {q.note}")
                    if "Access Denied" in q.note:
                        denied += 1
                        if denied >= 3:
                            print(f"  {h['name']} 連續被官網擋，這次先停止，保留前次資料")
                            break
                        continue   # 被擋的結果不寫入，避免蓋掉前次價格
                    denied = 0
                    if q.status != "error":      # 失敗不寫入，保留之前抓到的價格
                        db.save_quote(q, fetch_day)
                    if delay and i < len(dates):
                        if h["chain"] == "ihg" and not h.get("url_template"):
                            await asyncio.sleep(random.uniform(0.5, 1.5))
                        else:
                            await asyncio.sleep(random.uniform(lo, hi))
                results[h["id"]] = res
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "watch", "deals", "report", "login", "test-line", "poll"])
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--fetch-day", default=date.today().isoformat())
    ap.add_argument("--no-publish", action="store_true", help="不上傳到 GitHub")
    a = ap.parse_args()

    cfg = load(a.config)
    if a.cmd == "test-line":
        ok = notify.send_line(cfg, "房價監控：LINE 通知測試成功")
        print("LINE 測試", "成功" if ok else "失敗（請檢查 data/line.json）")
        return
    pending = None
    if a.cmd == "poll":   # 先看有沒有請求，沒有就安靜結束（每分鐘執行，不讀清單、不寫 log）
        db0 = DB(cfg.get("db_path", "data/hotelmon.db"))
        pending = pending_requests(cfg, db0)
        if not pending:
            return
    if cfg.get("github", {}).get("repo") and not cfg.get("use_local_watchlist"):
        w = watchlist.load(cfg)
        if w["hotels"]:
            cfg["hotels"], cfg["monitors"] = w["hotels"], w["monitors"]
    db = DB(cfg.get("db_path", "data/hotelmon.db"))
    hotels = [h for h in cfg.get("hotels", []) if h.get("enabled", True)]
    by_id = {h["id"]: h for h in hotels}

    if a.cmd == "login":
        asyncio.run(login(cfg))
        return

    if a.cmd == "poll":
        return poll(a, cfg, db, hotels, by_id, pending)

    lock = Path(cfg.get("data_dir", "data")) / "run.lock"
    if a.cmd in ("daily", "watch"):
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists() and time.time() - lock.stat().st_mtime < 90 * 60:
            print("另一個抓取還在執行，這次略過")
            return
        lock.write_text(a.cmd)
    try:
        run_cmd(a, cfg, db, hotels, by_id)
    finally:
        if a.cmd in ("daily", "watch"):
            lock.unlink(missing_ok=True)
    if a.cmd in ("daily", "watch"):
        ensure_poll_task()


def ensure_poll_task():
    """Windows：確保有每分鐘檢查手機請求的排程（用 pythonw，不會跳出視窗）。"""
    if os.name != "nt":
        return
    name = "HotelMon Poll"
    if subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True).returncode == 0:
        return
    pyw = BASE / ".venv" / "Scripts" / "pythonw.exe"
    tr = f'"{pyw}" "{BASE / "run.py"}" poll'
    r = subprocess.run(["schtasks", "/Create", "/F", "/TN", name, "/SC", "MINUTE", "/MO", "1", "/TR", tr],
                       capture_output=True, text=True)
    print("建立手機請求排程：", "成功" if r.returncode == 0 else r.stderr.strip())


def month_dates(month: str, today: date, horizon: int = 366):
    y, m = map(int, month.split("-"))
    last = today + timedelta(days=horizon)
    return {date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)
            if today <= date(y, m, d) <= last}


def pending_requests(cfg, db):
    """手機網頁送來的請求：watch/requests.json 的 [{id, t, type, hotel, month}]"""
    try:
        reqs = watchlist.read_repo_json(cfg, cfg.get("github", {}).get("requests_path", "watch/requests.json")) or []
    except Exception:  # noqa: BLE001
        return []
    now = time.time()
    return [r for r in reqs if r.get("id") and not db.was_sent("req:" + str(r["id"]))
            and now - float(r.get("t", 0)) / 1000 < 6 * 3600]


def poll(a, cfg, db, hotels, by_id, pending):
    now = time.time()
    lock = Path(cfg.get("data_dir", "data")) / "run.lock"
    if lock.exists() and now - lock.stat().st_mtime < 90 * 60:
        return   # 排程正在跑，下一分鐘再來
    lock.write_text("poll")
    try:
        today = date.today()
        targets = {}
        full = any(r.get("type") == "daily" for r in pending)
        if full:   # 手機按「立即完整更新」：所有收藏飯店，範圍同每日排程
            for h in hotels:
                targets.setdefault(h["id"], set()).update(target_dates(cfg, h, "daily"))
        for r in pending:
            h = by_id.get(r.get("hotel"))
            if h and r.get("month"):
                ds = month_dates(r["month"], today)
                if r.get("type") == "month":   # 60 天內由每日排程負責，這裡只補 60 天以後
                    lim = today + timedelta(days=int(h.get("days_ahead", cfg.get("days_ahead", 60))) - 1)
                    ds = {d for d in ds if d > lim}
                targets.setdefault(h["id"], set()).update(ds)
            if r.get("type") == "open":
                for m in cfg.get("monitors", []):
                    d = str(m["date"])
                    if m["hotel"] in by_id and d >= today.isoformat():
                        targets.setdefault(m["hotel"], set()).add(date.fromisoformat(d))
        print(f"[poll] {datetime.now():%m-%d %H:%M} 處理 {len(pending)} 個請求：" +
              ", ".join(f"{by_id[k]['name']} {len(v)} 天" for k, v in targets.items()))
        msgs = []
        if targets:
            results = asyncio.run(collect(cfg, db, hotels, "poll", a.fetch_day, targets))
            msgs += alerts.check_monitors(db, cfg, by_id)
            for h in hotels:
                msgs += alerts.check_errors(db, h, results.get(h["id"], []), a.fetch_day)
                if full:
                    msgs += alerts.check_drops(db, cfg, h, a.fetch_day)
        for r in pending:
            db.mark_sent("req:" + str(r["id"]))
        for m in msgs:
            notify.send(cfg, m)
        out = report.write(db, cfg, hotels)
        publish.publish(cfg, out)
    finally:
        lock.unlink(missing_ok=True)


def run_cmd(a, cfg, db, hotels, by_id):
    msgs = []
    # LINE 第一次設定好時送一則連線確認
    if not db.was_sent("line-hello") and notify._line_cfg(cfg).get("channel_token"):
        if notify.send_line(cfg, "房價監控已連上 LINE。之後監控達標、降價、抓取失敗都會傳到這裡。"):
            db.mark_sent("line-hello")
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
        out = report.write(db, cfg, hotels)
        print("日曆頁：", out)
        if not a.no_publish:
            publish.publish(cfg, out)


async def login(cfg):
    """開本程式專用的 Chrome，讓你登入萬豪、IHG 等會員（之後抓價沿用登入狀態，可選）。"""
    from hotelmon.fetchers import BrowserFetcher
    cfg = dict(cfg)
    cfg["browser"] = dict(cfg.get("browser", {}), offscreen=False)
    async with BrowserFetcher(cfg) as f:
        for url in ["https://www.marriott.com/sign-in.mi", "https://www.ihg.com/rewardsclub/us/en/sign-in"]:
            p = await f.ctx.new_page()
            await p.goto(url)
        await asyncio.get_running_loop().run_in_executor(
            None, input, "請在開啟的 Chrome 視窗登入，完成後回到這裡按 Enter 關閉…")


if __name__ == "__main__":
    main()
