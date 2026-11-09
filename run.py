#!/usr/bin/env python3
"""飯店房價監控

  python run.py daily     週日抓所有飯店未來 N 天房價，其他天只查監控日期（--full 強制完整）→ 通知 → 產生日曆頁
  python run.py watch     只抓 monitors 裡的日期 → 通知（建議每 30–60 分鐘）
  python run.py deals     只檢查優惠 RSS
  python run.py report    只重產日曆頁
  python run.py login     開瀏覽器讓你登入各集團會員（登入狀態會保留）
  python run.py poll      檢查手機網頁送來的更新請求（每分鐘由排程執行，沒請求就立即結束）
  python run.py ihg-dir   立即更新 IHG 飯店清單（平常由 daily 每 30 天自動更新）

  共用參數：-c config.yaml   --fetch-day YYYY-MM-DD（測試用，模擬某天抓取）
"""
import argparse
import asyncio
import calendar
import json
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
class _Stamped:
    """log 每行前面加時間，被擋時才查得出是哪一次。"""
    def __init__(self, f):
        self.f, self.bol = f, True

    def write(self, s):
        out = []
        for part in s.splitlines(True):
            if self.bol:
                out.append(datetime.now().strftime("%m-%d %H:%M:%S "))
            out.append(part)
            self.bol = part.endswith("\n")
        self.f.write("".join(out))
        return len(s)

    def flush(self):
        self.f.flush()


if sys.stdout is None or sys.stderr is None:   # pythonw 沒有主控台，輸出寫到 log
    (BASE / "data").mkdir(exist_ok=True)
    _name = next((c for c in sys.argv[1:] if c in ("daily", "watch")), "poll")
    _log = open(BASE / "data" / f"{_name}.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _Stamped(_log)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from hotelmon import alerts, deals, ihgdir, notify, publish, report, ustatz, watchlist
from hotelmon.db import DB
from hotelmon.alerts import OFFICIAL
from hotelmon.fetchers import BrowserFetcher, DemoFetcher, Quote

IHG_RANGE_MIN = 3   # IHG 超過這麼多天就改用月曆 API


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


def update_marriott(cfg, db, hotels, fetch_day, now=None):
    """萬豪（未開 marriott_api）每間飯店每天 07:00 後讀一次 ustatz，成功就不再讀；失敗照封鎖間隔重試。"""
    now = now or datetime.now()
    results = {}
    if now.hour < 7 or cfg.get("marriott_api"):
        return results
    for h in hotels:
        key = f"mar:{now.date()}:{h['id']}"
        if h["chain"] != "marriott" or db.was_sent(key):
            continue
        if results:
            time.sleep(random.uniform(3, 6))
        try:
            qs = ustatz.fetch(h)
        except Exception as e:  # noqa: BLE001
            print(f"  [ustatz] {h['name']} 失敗：{e!s:.120}")
            results[h["id"]] = [Quote(h["id"], now.date(), None, None, "error", f"ustatz 讀取失敗：{e!s:.80}")]
            continue
        official = {d for d, s in db.snapshots(h["id"], now.date().isoformat()).items()
                    if s[0]["note"] == OFFICIAL and s[0]["fetch_day"] == fetch_day}   # 官網今天查過的日期不蓋掉
        for q in qs:
            if q.stay_date.isoformat() not in official:
                db.save_quote(q, fetch_day)
        print(f"  [ustatz] {h['name']} {len(qs)} 天")
        db.mark_sent(key)
        results[h["id"]] = qs
    return results


async def update_marriott_official(cfg, db, hotels, fetch_day, now=None):
    """萬豪監控日期每 8 小時（07、15、23 點起算）用官網月曆 API 查一次；失敗等下一輪，被封鎖就等到下一個 07:00。回傳要通知的訊息。"""
    now = now or datetime.now()
    shifted = now - timedelta(hours=7)   # 一天從 07:00 算起
    blocked_key = f"maroff-blocked:{shifted.date()}"
    if cfg.get("marriott_api") or db.was_sent(blocked_key):
        return []
    targets = {}
    for m in cfg.get("monitors", []):
        d = str(m["date"])
        key = f"maroff:{shifted.date()}:{shifted.hour // 8}:{m['hotel']}:{d}"
        if d >= now.date().isoformat() and any(h["id"] == m["hotel"] and h["chain"] == "marriott" for h in hotels) \
                and not db.was_sent(key):
            db.mark_sent(key)
            targets.setdefault(m["hotel"], set()).add(date.fromisoformat(d))
    if not targets:
        return []
    failed = []
    blocked = False
    try:
        async with BrowserFetcher(cfg) as f:
            for h in hotels:
                if h["id"] not in targets:
                    continue
                qs = None if f.mar_blocked else await f.marriott_range(dict(h, cash=False), sorted(targets[h["id"]]))
                blocked = blocked or f.mar_blocked
                if qs is None:
                    failed.append(h["name"])
                    continue
                for q in qs:
                    prev = db.snapshots(h["id"], q.stay_date.isoformat()).get(q.stay_date.isoformat())
                    if prev:   # 官網這裡只查點數，現金沿用 ustatz
                        q.cash, q.cash_unit = prev[0]["cash"], prev[0]["cash_unit"]
                    q.note = OFFICIAL
                    db.save_quote(q, fetch_day)
                    print(f"  [官網] {h['name']} {q.stay_date} {q.status} {q.price or ''}")
    except Exception as e:  # noqa: BLE001
        print(f"  [官網] 失敗：{e!s:.120}")
        failed = [h["name"] for h in hotels if h["id"] in targets]
    if not failed:
        return []
    print(f"  [官網] 查不到：{'、'.join(failed)}{'（被封鎖）' if blocked else ''}")
    if blocked:
        db.mark_sent(blocked_key)
        return [f"【萬豪官網】被封鎖，查不到 {'、'.join(failed)}，監控日期先用 ustatz 資料，下一個 07:00 後再試"]
    return [f"【萬豪官網】查不到 {'、'.join(failed)}，監控日期先用 ustatz 資料，下一輪（8 小時內）再試"]


async def collect(cfg, db, hotels, mode, fetch_day, targets=None):
    results = {}
    if targets is None:
        targets = {h["id"]: target_dates(cfg, h, mode, db) for h in hotels}
    hotels = [h for h in hotels if targets.get(h["id"])]   # 沒有要查的日期就不開瀏覽器、不連官網
    lo, hi = cfg.get("browser", {}).get("delay_seconds", [4, 9])
    ilo, ihi = cfg.get("browser", {}).get("ihg_delay_seconds", [3, 6])
    cap = int(cfg.get("ihg_daily_cap", 150))
    stopped = set()   # 這次執行已被擋的集團：同集團其他飯店也不再查
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
                dates = sorted(targets[h["id"]])
                # 萬豪：先用價格月曆 API 一次查整段，失敗才逐日開網頁
                if cfg.get("marriott_api") and h["chain"] == "marriott" and hasattr(f, "marriott_range") \
                        and h.get("mode", "points") == "points" and not f.mar_blocked:
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
                if h["chain"] in stopped or (h["chain"] == "marriott" and getattr(f, "mar_blocked", False)):
                    print(f"  {h['name']} 被官網擋，這次先停止，保留前次資料")   # 被擋就停，再查只會延長封鎖
                    results[h["id"]] = [Quote(h["id"], d, None, None, "error", "被官網防火牆擋下（Access Denied）") for d in dates]
                    continue
                counted = h["chain"] == "ihg"
                calls_key = f"calls:ihg:{date.today()}"
                # IHG 多天：月曆 API 一次查一個月（只有點數，現金沿用前次）；監控日期等少數日期仍逐日查（有現金）
                if counted and len(dates) > IHG_RANGE_MIN and hasattr(f, "ihg_range") \
                        and h.get("mode", "points") == "points" and not h.get("url_template"):
                    if db.counter(calls_key) >= cap:
                        print(f"  IHG 今天已查 {cap} 次，達上限，其餘日期明天再查")
                        results[h["id"]] = []
                        continue
                    try:
                        qs, n = await f.ihg_range(h, dates)
                    except Exception as e:  # noqa: BLE001
                        print(f"  [ihg] 月曆 API 失敗：{e!s:.120}")
                        qs, n = None, 1
                    for _ in range(n):
                        db.bump(calls_key)
                    if qs and alerts.is_denied(qs[0].note):
                        print(f"  {h['name']} 被官網擋，這次先停止同集團全部查詢，保留前次資料")
                        stopped.add(h["chain"])
                        results[h["id"]] = qs
                        continue
                    if qs is not None:
                        for q in qs:
                            prev = db.snapshots(h["id"], q.stay_date.isoformat()).get(q.stay_date.isoformat())
                            if prev:
                                q.cash, q.cash_unit = prev[0]["cash"], prev[0]["cash_unit"]
                            db.save_quote(q, fetch_day)
                            print(f"  {h['name']} {q.stay_date} {q.status} {q.price or ''} [月曆]")
                        results[h["id"]] = qs
                        await asyncio.sleep(random.uniform(ilo, ihi))
                        continue
                for i, d in enumerate(dates, 1):
                    if counted and db.counter(calls_key) >= cap:
                        print(f"  IHG 今天已查 {cap} 次，達上限，其餘日期明天再查")
                        break
                    q = await f.fetch(h, d)
                    if counted:
                        db.bump(calls_key)
                    res.append(q)
                    print(f"  {h['name']} {d} {q.status} {q.price or ''} {q.note:.80}")
                    if alerts.is_denied(q.note):
                        print(f"  {h['name']} 被官網擋，這次先停止同集團全部查詢，保留前次資料")
                        stopped.add(h["chain"])
                        if h["chain"] == "marriott":
                            f.mar_blocked = True
                        break   # 被擋的結果不寫入，避免蓋掉前次價格
                    if q.status != "error":      # 失敗不寫入，保留之前抓到的價格
                        db.save_quote(q, fetch_day)
                    if delay and i < len(dates):
                        if h["chain"] == "ihg" and not h.get("url_template"):
                            await asyncio.sleep(random.uniform(ilo, ihi))
                        else:
                            await asyncio.sleep(random.uniform(lo, hi))
                results[h["id"]] = res
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "watch", "deals", "report", "login", "test-line", "poll", "ihg-dir"])
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--fetch-day", default=date.today().isoformat())
    ap.add_argument("--no-publish", action="store_true", help="不上傳到 GitHub")
    ap.add_argument("--full", action="store_true", help="daily 不管星期幾都跑完整 60 天")
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
        if not pending or only_waiting(db0, pending):
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

    if a.cmd == "daily" and not (a.full or weekly_full_due(db)):
        a.cmd = "watch"   # 平常只查監控日期，完整 60 天每週日跑一次

    lock = Path(cfg.get("data_dir", "data")) / "run.lock"
    if a.cmd in ("daily", "watch", "ihg-dir"):
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock_busy(lock):
            print("另一個抓取還在執行，這次略過")
            return
        lock.write_text(str(os.getpid()))
    try:
        if a.cmd == "ihg-dir":
            refresh_ihg_dir(cfg, db, force=True)
        else:
            run_cmd(a, cfg, db, hotels, by_id)
        if a.cmd == "daily":
            db.mark_sent(f"full:{date.today()}")
        if a.cmd == "daily" and not a.no_publish and cfg.get("github", {}).get("repo"):
            refresh_ihg_dir(cfg, db)
    finally:
        if a.cmd in ("daily", "watch", "ihg-dir"):
            lock.unlink(missing_ok=True)
    if a.cmd in ("daily", "watch"):
        ensure_poll_task()


def ihg_watch_due(cfg, db, now=None):
    """Watch 每 30 分鐘跑，IHG 監控日期預設每 1 小時才查一次（排程時間會差一兩分鐘，留 10 分鐘彈性）。"""
    last = db.was_sent("ihg-watch")
    now = now or datetime.now()
    gap = timedelta(minutes=int(cfg.get("ihg_watch_minutes", 60)) - 10)
    return not last or now - datetime.strptime(last, "%Y-%m-%d %H:%M") >= gap


def weekly_full_due(db, today=None):
    """週日第一次 daily 才跑完整 60 天；排程一天兩次，跑完後第二次就只查監控日期。"""
    today = today or date.today()
    return today.weekday() == 6 and not db.was_sent(f"full:{today}")


def refresh_ihg_dir(cfg, db, force=False):
    """每 30 天更新一次 IHG 飯店清單（由 daily 觸發）；失敗就沿用舊清單，下次 daily 再試。"""
    last = db.was_sent("ihg-dir")
    if not force and last and (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M")).days < 30:
        return
    try:
        rows = asyncio.run(ihgdir.fetch(cfg))
    except Exception as e:  # noqa: BLE001
        print(f"[ihg-dir] 更新失敗：{e!s:.150}")
        return
    if len(rows) < 3000:
        print(f"[ihg-dir] 只抓到 {len(rows)} 間，太少，沿用舊清單")
        return
    out = Path(cfg.get("output_dir", "dist")) / "hotels-ihg.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[ihg-dir] {len(rows)} 間")
    if publish.publish(cfg, out, "site/hotels-ihg.json", "更新 IHG 飯店清單"):
        db.mark_sent("ihg-dir")


def ensure_poll_task():
    """Windows：確保有每分鐘檢查手機請求的排程（用 pythonw，不會跳出視窗）。"""
    if os.name != "nt":
        return
    name = "HotelMon Poll"
    if subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True,
                      creationflags=subprocess.CREATE_NO_WINDOW).returncode == 0:
        return
    pyw = BASE / ".venv" / "Scripts" / "pythonw.exe"
    tr = f'"{pyw}" "{BASE / "run.py"}" poll'
    r = subprocess.run(["schtasks", "/Create", "/F", "/TN", name, "/SC", "MINUTE", "/MO", "1", "/TR", tr],
                       capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
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


def only_waiting(db, pending):
    """所有請求都已試過、正在等封鎖解除（這段時間每分鐘的 poll 不讀清單、不寫 log）。"""
    blocked = alerts.blocked_chains(db)
    return (blocked and all(t > datetime.now() for t in blocked.values())
            and all(db.was_sent("reqw:" + str(r["id"])) for r in pending))


def request_targets(r, cfg, hotels, by_id, today):
    """一個手機請求要查的日期：{飯店 id: {日期}}"""
    t = {}
    if r.get("type") == "daily":   # 手機按「立即完整更新」：範圍同每日排程；chains 指定集團（IHG 網頁已自己查）
        for h in hotels:
            if not r.get("chains") or h["chain"] in r["chains"]:
                t.setdefault(h["id"], set()).update(target_dates(cfg, h, "daily"))
    h = by_id.get(r.get("hotel"))
    if h and r.get("month"):
        ds = month_dates(r["month"], today)
        t.setdefault(h["id"], set()).update(ds)
    if r.get("type") == "open":
        for m in cfg.get("monitors", []):
            d = str(m["date"])
            if m["hotel"] in by_id and d >= today.isoformat() \
                    and (not r.get("chains") or by_id[m["hotel"]]["chain"] in r["chains"]):
                t.setdefault(m["hotel"], set()).add(date.fromisoformat(d))
    # 萬豪每天排程查一次，手機請求不觸發
    return {k: v for k, v in t.items() if v and (cfg.get("marriott_api") or by_id[k]["chain"] != "marriott")}


def lock_busy(lock):
    """有抓取正在執行。程序被中斷時不會清 lock，所以檢查寫 lock 的程序是否還在；最多等 90 分鐘。"""
    if not lock.exists() or time.time() - lock.stat().st_mtime >= 90 * 60:
        return False
    pid = lock.read_text().strip()
    if not pid.isdigit() or os.name != "nt":
        return True
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return f'"{pid}"' in r.stdout


def poll(a, cfg, db, hotels, by_id, pending):
    lock = Path(cfg.get("data_dir", "data")) / "run.lock"
    if lock_busy(lock):
        return   # 排程正在跑，下一分鐘再來
    lock.write_text(str(os.getpid()))
    try:
        today = date.today()
        wait = {c for c, t in alerts.blocked_chains(db).items() if t > datetime.now()}
        plans = {}   # 每個請求還沒完成的飯店（同一請求已成功的飯店，重試時不再查）
        targets = {}
        for r in pending:
            plans[r["id"]] = {k: v for k, v in request_targets(r, cfg, hotels, by_id, today).items()
                              if not db.was_sent(f"reqh:{r['id']}:{k}")}
            for k, v in plans[r["id"]].items():
                if by_id[k]["chain"] not in wait:
                    targets.setdefault(k, set()).update(v)
        full = any(r.get("type") == "daily" for r in pending)
        results = {}
        msgs = []
        if targets:
            print(f"[poll] {datetime.now():%m-%d %H:%M} 處理 {len(pending)} 個請求：" +
                  ", ".join(f"{by_id[k]['name']} {len(v)} 天" for k, v in targets.items()))
            results = asyncio.run(collect(cfg, db, hotels, "poll", a.fetch_day, targets))
            alerts.update_blocks(db, hotels, results)
            msgs += alerts.check_monitors(db, cfg, by_id)
            for h in hotels:
                msgs += alerts.check_errors(db, h, results.get(h["id"], []), a.fetch_day)
                if full and h["id"] in results:
                    msgs += alerts.check_drops(db, cfg, h, a.fetch_day)
        finished = 0
        for r in pending:
            left = False
            for k in plans[r["id"]]:
                if k in targets and not alerts.hotel_failed(results.get(k)):
                    db.mark_sent(f"reqh:{r['id']}:{k}")
                else:
                    left = True
            if left:   # 被擋：不標完成，等 BLOCK_MINUTES 後重試，超過 6 小時就放棄
                db.mark_sent("reqw:" + str(r["id"]))
            else:
                db.mark_sent("req:" + str(r["id"]))
                finished += 1
        if not targets and not finished:
            return   # 全部都在等封鎖解除
        for m in msgs:
            notify.send(cfg, m)
        out = report.write(db, cfg, hotels)
        publish.publish_data(cfg, out)
    finally:
        lock.unlink(missing_ok=True)


def run_cmd(a, cfg, db, hotels, by_id):
    msgs = []
    # LINE 第一次設定好時送一則連線確認
    if not db.was_sent("line-hello") and notify._line_cfg(cfg).get("channel_token"):
        if notify.send_line(cfg, "房價監控已連上 LINE。之後監控達標、降價、抓取失敗都會傳到這裡。"):
            db.mark_sent("line-hello")
    if a.cmd in ("daily", "watch"):
        run_hotels = alerts.not_blocked(db, hotels)
        results = update_marriott(cfg, db, run_hotels, a.fetch_day)
        browser_hotels = [h for h in run_hotels if h["chain"] != "marriott" or cfg.get("marriott_api")]
        if a.cmd == "watch" and any(h["chain"] == "ihg" for h in browser_hotels):
            if ihg_watch_due(cfg, db):
                db.mark_sent("ihg-watch")
            else:   # IHG 監控日期每 ihg_watch_minutes 才查；還沒資料的新飯店照抓
                browser_hotels = [h for h in browser_hotels if h["chain"] != "ihg" or not db.last_fetch(h["id"])]
        results.update(asyncio.run(collect(cfg, db, browser_hotels, a.cmd, a.fetch_day)))
        msgs += asyncio.run(update_marriott_official(cfg, db, hotels, a.fetch_day))
        alerts.update_blocks(db, hotels, results)
        msgs += alerts.check_monitors(db, cfg, by_id)
        for h in hotels:
            msgs += alerts.check_errors(db, h, results.get(h["id"], []), a.fetch_day)
            if a.cmd == "daily":
                msgs += alerts.check_drops(db, cfg, h, a.fetch_day)
    if a.cmd in ("daily", "watch"):
        warning = publish.token_warning(cfg, db)
        if warning:
            msgs.append(warning)
    if a.cmd in ("daily", "watch", "deals"):
        msgs += deals.check_deals(db, cfg)
    for m in msgs:
        notify.send(cfg, m)
    if a.cmd in ("daily", "watch", "report"):
        out = report.write(db, cfg, hotels)
        print("日曆頁：", out)
        if not a.no_publish:
            (publish.publish if a.cmd == "report" else publish.publish_data)(cfg, out)


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
