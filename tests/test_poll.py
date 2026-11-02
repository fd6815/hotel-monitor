"""測 poll 與 collect：不連官網、不發通知、不上傳，資料庫與 lock 都放暫存資料夾。
執行：.venv\\Scripts\\python.exe tests\\test_poll.py
"""
import asyncio, sys, tempfile, types
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run
from hotelmon import alerts
from hotelmon.db import DB

tmp = Path(tempfile.mkdtemp())
cfg = {"db_path": str(tmp / "t.db"), "data_dir": str(tmp), "days_ahead": 60, "github": {}, "marriott_api": True,
       "monitors": [{"hotel": "b", "date": (date.today() + timedelta(days=100)).isoformat(), "max_price": 1}]}
hotels = [{"id": "a", "name": "A", "chain": "ihg", "code": "AAAAA"},
          {"id": "b", "name": "B", "chain": "marriott", "code": "BBBBB"}]
by_id = {h["id"]: h for h in hotels}
args = types.SimpleNamespace(fetch_day=date.today().isoformat())

calls = {"drops": 0, "published": 0, "collect": 0}
captured = {}
denied = {"on": False}


def Q(note="", status="ok"):
    return types.SimpleNamespace(status=status, note=note, stay_date=date.today(), price=1, unit="pts")


real_collect = run.collect


class FakeBrowser:
    """假的 BrowserFetcher：marriott_range 回傳 None，blocked 決定是不是被擋。"""
    blocked = False

    def __init__(self, cfg):
        self.mar_blocked = False
        self.range_calls, self.fetch_calls = [], []
        FakeBrowser.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def marriott_range(self, hotel, dates):
        self.range_calls.append(hotel["id"])
        self.mar_blocked = FakeBrowser.blocked
        return None

    async def fetch(self, hotel, d):
        self.fetch_calls.append(hotel["id"])
        return run.Quote(hotel["id"], d, 100, "pts", "ok")


def test_collect():
    mar = [{"id": "m1", "name": "M1", "chain": "marriott", "code": "M1"},
           {"id": "m2", "name": "M2", "chain": "marriott", "code": "M2"}]
    days = {date.today() + timedelta(days=i) for i in range(3)}
    c = {"browser": {"delay_seconds": [0, 0]}, "marriott_api": True}
    tdb = DB(str(tmp / "c.db"))
    saved = run.BrowserFetcher
    run.BrowserFetcher = FakeBrowser
    try:
        # 被擋：不逐日開網頁，第二間也不再呼叫 API，結果算失敗
        FakeBrowser.blocked = True
        res = asyncio.run(real_collect(c, tdb, mar, "poll", args.fetch_day, {"m1": days, "m2": days}))
        f = FakeBrowser.last
        assert f.range_calls == ["m1"] and f.fetch_calls == [], (f.range_calls, f.fetch_calls)
        assert alerts.hotel_failed(res["m1"]) and alerts.hotel_failed(res["m2"])
        # API 壞了但不是被擋：退回逐日
        FakeBrowser.blocked = False
        res = asyncio.run(real_collect(c, tdb, mar[:1], "poll", args.fetch_day, {"m1": days}))
        assert len(FakeBrowser.last.fetch_calls) == 3 and not alerts.hotel_failed(res["m1"])
        # 預設不用 API：逐日開網頁，第一次被擋就全部停止
        c.pop("marriott_api")
        res = asyncio.run(real_collect(c, tdb, mar[:1], "poll", args.fetch_day, {"m1": days}))
        assert FakeBrowser.last.range_calls == [] and len(FakeBrowser.last.fetch_calls) == 3
        real_fetch = FakeBrowser.fetch
        async def denied_fetch(self, hotel, d):
            self.fetch_calls.append(hotel["id"])
            return run.Quote(hotel["id"], d, None, None, "error", "被官網防火牆擋下（Access Denied）")
        FakeBrowser.fetch = denied_fetch
        res = asyncio.run(real_collect(c, tdb, mar, "poll", args.fetch_day, {"m1": days, "m2": days}))
        FakeBrowser.fetch = real_fetch
        assert FakeBrowser.last.fetch_calls == ["m1"] and alerts.hotel_failed(res["m1"]) and alerts.hotel_failed(res["m2"])
    finally:
        run.BrowserFetcher = saved


async def fake_collect(cfg, db, hotels, mode, fetch_day, targets=None):
    calls["collect"] += 1
    captured.clear()
    captured.update(targets or {})
    res = {}
    for k in captured:
        if by_id[k]["chain"] == "marriott" and denied["on"]:
            res[k] = [Q("被官網防火牆擋下（Access Denied）", "error")] * 3
        else:
            res[k] = [Q()]
    return res

run.collect = fake_collect
alerts.check_drops = lambda *a, **k: calls.__setitem__("drops", calls["drops"] + 1) or []
alerts.check_errors = lambda *a, **k: []
run.report.write = lambda *a, **k: tmp / "data.json"
real_publish_data = run.publish.publish_data
run.publish.publish_data = lambda *a, **k: calls.__setitem__("published", calls["published"] + 1)
run.notify.send = lambda *a, **k: None
db = DB(cfg["db_path"])


def poll(*reqs):
    run.poll(args, cfg, db, hotels, by_id, list(reqs))


# 正常：daily 查全部並完成
r1 = {"id": "r1", "type": "daily", "t": 0}
poll(r1)
assert set(captured) == {"a", "b"} and len(captured["a"]) == 60 and len(captured["b"]) == 61, captured
assert calls["drops"] == 2 and calls["published"] == 1, calls
assert db.was_sent("req:r1") and not (tmp / "run.lock").exists()

# month 查整個月（包含 60 天內），不跑降價檢查
calls["drops"] = 0
m = (date.today() + timedelta(days=120)).strftime("%Y-%m")
poll({"id": "r2", "type": "month", "hotel": "a", "month": m, "t": 0})
assert set(captured) == {"a"} and calls["drops"] == 0 and db.was_sent("req:r2"), captured
poll({"id": "r2b", "type": "month", "hotel": "a", "month": date.today().strftime("%Y-%m"), "t": 0})
assert captured["a"] and min(captured["a"]) == date.today() and db.was_sent("req:r2b"), captured

# 被擋：IHG 完成、萬豪不標完成，寫入封鎖
denied["on"] = True
r3 = {"id": "r3", "type": "daily", "t": 0}
poll(r3)
assert not db.was_sent("req:r3") and db.was_sent("reqw:r3") and db.was_sent("reqh:r3:a") and not db.was_sent("reqh:r3:b")
assert "marriott" in alerts.blocked_chains(db)
assert run.only_waiting(db, [r3])
# 新請求還沒試過，不算等待中
assert not run.only_waiting(db, [r3, {"id": "new", "type": "open", "t": 0}])

# 封鎖期間：不抓、不上傳
n, p = calls["collect"], calls["published"]
poll(r3)
assert calls["collect"] == n and calls["published"] == p and not db.was_sent("req:r3"), calls
assert not (tmp / "run.lock").exists()

# 封鎖期間的新 IHG 請求仍可處理；萬豪 month 請求只等待
poll({"id": "r4", "type": "month", "hotel": "a", "month": m, "t": 0})
assert calls["collect"] == n + 1 and db.was_sent("req:r4"), calls
poll({"id": "r5", "type": "month", "hotel": "b", "month": m, "t": 0})
assert calls["collect"] == n + 1 and db.was_sent("reqw:r5") and not db.was_sent("req:r5"), calls

# 30 分鐘到、仍被擋：重試一次（只查萬豪）後延長封鎖
old = (datetime.now() - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M")
db.con.execute("UPDATE alerts_sent SET sent_at=? WHERE key='blocked:marriott'", (old,)); db.con.commit()
assert not run.only_waiting(db, [r3])
poll(r3)
assert set(captured) == {"b"} and not db.was_sent("req:r3"), captured
assert run.only_waiting(db, [r3])

# 到期且解封：完成，封鎖清除
denied["on"] = False
db.con.execute("UPDATE alerts_sent SET sent_at=? WHERE key='blocked:marriott'", (old,)); db.con.commit()
poll(r3)
assert set(captured) == {"b"} and db.was_sent("req:r3") and not alerts.blocked_chains(db), captured

# 部分日期出錯（非被擋）算成功
assert not alerts.hotel_failed([Q(), Q("timeout", "error")])
assert alerts.hotel_failed([Q("x", "error")]) and not alerts.hotel_failed(None) and not alerts.hotel_failed([])
# 瀏覽器中途關閉：前面成功、後面全錯，要算失敗（00:22 真實情況：11 筆成功、51 筆錯）
assert alerts.hotel_failed([Q()] * 11 + [Q("closed", "error")] * 51)

# watch 成功可解除封鎖
db.mark_sent("blocked:marriott")
alerts.update_blocks(db, hotels, {"b": [Q()]})
assert not alerts.blocked_chains(db)
# chains：網頁已自己查 IHG，電腦只查指定集團
poll({"id": "r6", "type": "daily", "chains": ["marriott"], "t": 0})
assert set(captured) == {"b"}, captured
poll({"id": "r7", "type": "open", "chains": ["ihg"], "t": 0})
assert db.was_sent("req:r7"), "監控只在萬豪，指定 ihg 時應直接完成"
poll({"id": "r8", "type": "open", "chains": ["marriott"], "t": 0})
assert set(captured) == {"b"} and len(captured["b"]) == 1, captured

test_collect()

# 封鎖重試間隔：最短 30 分鐘，隨被擋時間拉長，最長 6 小時；排程也會跳過
db4 = DB(str(tmp / "b.db"))
stamp = lambda m: (datetime.now() - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M")
def held(minutes):
    db4.con.execute("INSERT OR REPLACE INTO alerts_sent VALUES('blocked:marriott',?)", (stamp(0),))
    db4.con.execute("INSERT OR REPLACE INTO alerts_sent VALUES('blocked-since:marriott',?)", (stamp(minutes),))
    db4.con.commit()
    return round((alerts.blocked_chains(db4)["marriott"] - datetime.now()).total_seconds() / 60)
assert 28 <= held(0) <= 30 and 118 <= held(120) <= 120 and 358 <= held(1000) <= 360
assert alerts.not_blocked(db4, hotels) == [hotels[0]]
alerts.update_blocks(db4, hotels, {"b": [Q()]})
assert not db4.was_sent("blocked-since:marriott") and alerts.not_blocked(db4, hotels) == hotels

# 萬豪每間飯店每天 07:00 後讀一次 ustatz，失敗當天可重試；手機請求不觸發
from hotelmon import ustatz
d1, d2, d3 = (date.today() + timedelta(days=i) for i in (1, 2, 3))
html = "".join(f'eachObj={{ hotelname: "X", checkin: "{d}", awardPoints: "{p}", currency: "JPY", cash: "{c}", bookingUrl: "u" }};'
               for d, p, c in [(d1, "62000", "114000.5"), (d2, "無點數房", "90000"), (d3, "50000", "")])
rows = ustatz.parse('eachObj={ checkin: "2020-01-01", awardPoints: "1" };' + html, {"id": "b"})
assert [(q.stay_date, q.price, q.status, q.cash, q.cash_unit) for q in rows] == [
    (d1, 62000, "ok", 114000, "JPY"), (d2, None, "sold_out", 90000, "JPY"), (d3, 50000, "ok", None, None)], rows
c5 = {"days_ahead": 60, "monitors": []}
db5 = DB(str(tmp / "m.db"))
reads = []
def fake_fetch(h):
    reads.append(h["id"])
    if len(reads) == 1:
        raise RuntimeError("Cloudflare 503")
    return rows
real_ustatz_fetch, ustatz.fetch = ustatz.fetch, fake_fetch
early, later = datetime.now().replace(hour=6), datetime.now().replace(hour=7)
assert run.update_marriott(c5, db5, hotels, args.fetch_day, early) == {} and not reads
r5 = run.update_marriott(c5, db5, hotels, args.fetch_day, later)   # 失敗：不寫資料、算失敗
assert reads == ["b"] and alerts.hotel_failed(r5["b"]) and not db5.snapshots("b", args.fetch_day)
r5 = run.update_marriott(c5, db5, hotels, args.fetch_day, later)   # 重試成功
assert len(r5["b"]) == 3 and db5.snapshots("b", args.fetch_day)[d1.isoformat()][0]["price"] == 62000
assert run.update_marriott(c5, db5, hotels, args.fetch_day, later) == {} and len(reads) == 2   # 當天不再讀
assert run.update_marriott({"marriott_api": True}, db5, hotels, args.fetch_day, later) == {}
ustatz.fetch = real_ustatz_fetch
assert run.request_targets({"type": "daily"}, c5, hotels, by_id, date.today()).keys() == {"a"}
assert not run.request_targets({"type": "month", "hotel": "b", "month": "2027-01"}, c5, hotels, by_id, date.today())

# 萬豪到價：不結束監控，價格變了才再通知；IHG 通知一次就完成
db6 = DB(str(tmp / "n.db"))
md = (date.today() + timedelta(days=5)).isoformat()
c6 = {"monitors": [{"hotel": "b", "date": md, "max_price": 60000}, {"hotel": "a", "date": md, "max_price": 60000}]}
def price(h, p, day):
    db6.save_quote(run.Quote(h, date.fromisoformat(md), p, "pts", "ok"), day)
price("b", 59000, "2026-01-01"); price("a", 59000, "2026-01-01")
m6 = alerts.check_monitors(db6, c6, by_id)
assert len(m6) == 2 and "ustatz" in m6[0] and "ustatz" not in m6[1], m6
assert alerts.check_monitors(db6, c6, by_id) == []
price("b", 58000, "2026-01-02"); price("a", 58000, "2026-01-02")
m6 = alerts.check_monitors(db6, c6, by_id)
assert len(m6) == 1 and "58,000" in m6[0], m6
assert not db6.was_sent(alerts.monitor_key(c6["monitors"][0])) and db6.was_sent(alerts.monitor_key(c6["monitors"][1]))

# 萬豪監控日期每 8 小時查官網：蓋過 ustatz 點數、留現金、ustatz 重讀不蓋回；失敗等下一輪，被封鎖等到下一個 07:00
db7 = DB(str(tmp / "o.db"))
c7 = {"monitors": [{"hotel": "b", "date": md, "max_price": 60000}, {"hotel": "a", "date": md, "max_price": 60000}]}
mdd = date.fromisoformat(md)
db7.save_quote(run.Quote("b", mdd, 62000, "pts", "ok", cash=114000, cash_unit="JPY"), args.fetch_day)
ranges = []
class FakeBrowser:
    fail = None   # "denied" 被封鎖、"error" 其他失敗
    def __init__(self, cfg): self.mar_blocked = False
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): pass
    async def marriott_range(self, h, ds):
        ranges.append((h["id"], ds, h.get("cash")))
        if self.fail:
            self.mar_blocked = self.fail == "denied"
            return None
        return [run.Quote(h["id"], d, 59000, "pts", "ok") for d in ds]
real_browser, run.BrowserFetcher = run.BrowserFetcher, FakeBrowser
official = lambda c, db, when: asyncio.run(run.update_marriott_official(c, db, hotels, args.fetch_day, when))
at = lambda hour, days=0: later.replace(hour=hour) + timedelta(days=days)
assert official({**c7, "marriott_api": True}, db7, later) == [] and not ranges
assert official(c7, db7, later) == [] and ranges == [("b", [mdd], False)]   # 只查萬豪、不查現金
row = db7.snapshots("b", md)[md][0]
assert (row["price"], row["note"], row["cash"], row["cash_unit"]) == (59000, alerts.OFFICIAL, 114000, "JPY")
assert official(c7, db7, at(14)) == [] and len(ranges) == 1   # 同一輪不再查
assert official(c7, db7, at(15)) == [] and len(ranges) == 2
assert official(c7, db7, at(23)) == [] and len(ranges) == 3
assert official(c7, db7, at(6, 1)) == [] and len(ranges) == 3   # 隔天清晨還是 23 點那一輪
row = db7.snapshots("b", md)[md][0]
assert (row["cash"], row["cash_unit"]) == (114000, "JPY")
ustatz.fetch = lambda h: [run.Quote("b", mdd, 70000, "pts", "ok")]
db7.clear_sent(f"mar:{later.date()}:b")
run.update_marriott({}, db7, hotels, args.fetch_day, later)
ustatz.fetch = real_ustatz_fetch
assert db7.snapshots("b", md)[md][0]["price"] == 59000
m7 = alerts.check_monitors(db7, c7, by_id)   # 官網來源：沒有 ustatz 註記，通知一次就完成
assert len(m7) == 1 and "ustatz" not in m7[0] and db7.was_sent(alerts.monitor_key(c7["monitors"][0])), m7
db8 = DB(str(tmp / "p.db"))
FakeBrowser.fail, ranges[:] = "denied", []
m8 = official(c7, db8, later)
assert len(m8) == 1 and "被封鎖" in m8[0] and "ustatz" in m8[0] and not db8.snapshots("b", md), m8
for when in (at(15), at(23), at(6, 1)):   # 被封鎖：下一個 07:00 前都不查、不再通知
    assert official(c7, db8, when) == [] and len(ranges) == 1
FakeBrowser.fail = "error"
m8 = official(c7, db8, at(7, 1))           # 隔天再試；其他失敗不算封鎖，下一輪就再試
assert len(ranges) == 2 and len(m8) == 1 and "被封鎖" not in m8[0], m8
assert official(c7, db8, at(14, 1)) == [] and len(ranges) == 2
official(c7, db8, at(15, 1))
assert len(ranges) == 3
FakeBrowser.fail = None
run.BrowserFetcher = real_browser

# data.json 沒變不上傳；只有時間變也不上傳
import json
pubs = []
saved_pub, run.publish.publish = run.publish.publish, lambda c, p, *a, **k: pubs.append(p) or True
c7 = {"data_dir": str(tmp / "pub")}
(tmp / "pub").mkdir()
dj = tmp / "pub" / "data.json"
dj.write_text(json.dumps({"updated": "1", "hotels": [{"id": "a", "last": "1", "days": {}}]}))
assert real_publish_data(c7, dj) and len(pubs) == 1
dj.write_text(json.dumps({"updated": "2", "hotels": [{"id": "a", "last": "2", "days": {}}]}))
assert not real_publish_data(c7, dj) and len(pubs) == 1
dj.write_text(json.dumps({"updated": "2", "hotels": [{"id": "a", "last": "2", "days": {"x": 1}}]}))
assert real_publish_data(c7, dj) and len(pubs) == 2
run.publish.publish = saved_pub

# token 到期提醒：7 天內才提醒、每天一次；失效也提醒
db7 = DB(str(tmp / "tok.db"))
c8 = {"github": {"repo": "x/y", "token": "t"}}
def fake_gh(status, days):
    exp = (date.today() + timedelta(days=days)).strftime("%Y-%m-%d 14:44:50 UTC")
    return lambda *a, **k: types.SimpleNamespace(status_code=status, headers={"github-authentication-token-expiration": exp})
saved_get = run.publish.requests.get
run.publish.requests.get = fake_gh(200, 30)
assert run.publish.token_warning(c8, db7) is None
run.publish.requests.get = fake_gh(200, 5)
assert run.publish.token_warning(c8, db7) is None   # 今天查過了
db7.clear_sent(f"token-check:{date.today()}")
assert "還有 5 天" in run.publish.token_warning(c8, db7)
assert run.publish.token_warning(c8, db7) is None
db7.clear_sent(f"token-check:{date.today()}")
run.publish.requests.get = fake_gh(401, 5)
assert "已失效" in run.publish.token_warning(c8, db7)
run.publish.requests.get = saved_get

# 排程：週日第一次才完整
sun = date(2026, 11, 8)
assert run.weekly_full_due(db, sun) and not run.weekly_full_due(db, sun + timedelta(days=1))
db.mark_sent(f"full:{sun}")
assert not run.weekly_full_due(db, sun)

# 沒有要查的日期：不開瀏覽器
class NoBrowser:
    def __init__(self, cfg):
        raise AssertionError("不該開瀏覽器")
saved_browser, run.BrowserFetcher = run.BrowserFetcher, NoBrowser
db3 = DB(str(tmp / "w.db"))
db3.save_quote(run.Quote("x", date.today(), 1, "pts", "ok"), args.fetch_day)
assert asyncio.run(real_collect({"monitors": []}, db3, [{"id": "x", "name": "X", "chain": "ihg", "code": "XXXXX"}], "watch", args.fetch_day)) == {}
run.BrowserFetcher = saved_browser

# 優惠篩選
from hotelmon import deals
for t in ["IHG 15% Off Awards At Select New & Refreshed Hotels Through April 30, 2027",
          "GHA Discovery Double Elite Qualifying Nights November 1 – December 31, 2026",
          "Marriott Bonvoy Nightly Upgrade Awards: How To Earn & Redeem Them, Maximize Value",
          "ALL Accor Greater China 3X Points + Instant Gold With Fast Track Until December 31, 2026",
          "萬豪買點加碼 40% 優惠"]:
    assert deals.is_useful(t), t
for t in ["Massive Amadeus Hack Affecting Hundreds Of Thousands Of Passengers Across Multiple Airlines",
          "Rant: Entire Residential Apartment Buildings Being Converted To Air BnB’s & Hotels",
          "Weekly Review: October 31, 2026",
          "Grand Hyatt Barcelona Becomes RIU Plaza Hotel On April 1, 2027",
          "A sky-high Vegas bar, a chic new Marriott in South Africa and more hotel news",
          "Compensation Clinic: Hyatt Hotels Dining Pre-Order System Fail During Dinner"]:
    assert not deals.is_useful(t), t
for t in ["Buy IHG One Rewards Points With 100% Bonus", "Alaska Mileage Plan Sale: Buy Miles", "British Airways Avios Sale",
          "Mistake Fare: Tokyo To Paris", "Hyatt Status Match Offer", "ALL Accor 3X Points + Instant Gold With Fast Track"]:
    assert deals.is_alert(t), t
for t in ["GHA Discovery Double Elite Qualifying Nights", "How to earn and use an IHG annual lounge membership",
          "Marriott Points Sale 40% Off", "Transfer Chase Points To IHG With 80% Bonus: Why It’s Not Worth It"]:
    assert not deals.is_alert(t), t
for t in ["[限時優惠] 花旗卡點數兌換方案", "SAS北歐航空正夯，新手一路配對篇！", "IHG的里程碑：餐券到底怎麼用？值不值得換？", "2026/10月份的酒店會籍總整理來囉！"]:
    assert deals.is_useful(t), t
for t in ["開箱名古屋萬豪酒店(Nagoya Marriott Assocla Hotel)！", "[Nana住宿紀錄] Hyatt Regency Chicago",
          "Best-Ever Citi AAdvantage Executive Card 125K Bonus Miles Offer", "Am I eligible for the Chase Sapphire Preferred's 75,000-point bonus?"]:
    assert not deals.is_useful(t), t
assert deals.translate("已是中文") == "已是中文"
saved_get, deals.requests.get = deals.requests.get, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
assert deals.translate("offline") == "offline"
deals.requests.get = saved_get

# lock：程序還在才算忙；程序被中斷留下的 lock 不擋；舊格式依時間判斷
import os, subprocess
lk = tmp / "lk"
assert not run.lock_busy(lk)
lk.write_text(str(os.getpid()))
assert run.lock_busy(lk)
child = subprocess.Popen([sys.executable, "-c", "pass"])
child.wait()
lk.write_text(str(child.pid))
assert not run.lock_busy(lk)
lk.write_text("watch")
assert run.lock_busy(lk)
os.utime(lk, (0, 0))
assert not run.lock_busy(lk)

# IHG 清單：解析、30 天內不更新、太少或失敗不上傳
from hotelmon import ihgdir
rows = ihgdir.parse_rows(["https://www.ihg.com/intercontinental/hotels/us/en/taipei/tpepi/hoteldetail",
                          "https://www.ihg.com/spnd/hotels/us/en/taipei/tpepi/hoteldetail",
                          "https://www.ihg.com/voco/hotels/us/en/new-york/nycvo/hoteldetail",
                          "https://www.ihg.com/voco/hotels/us/en/new-york/nycvo/hoteldetail/rooms"])
assert rows == [["NYCVO", "voco", "new-york"], ["TPEPI", "intercontinental", "taipei"]], rows
ups = []
run.publish.publish = lambda c, p, path=None, message="": ups.append(path) or True
c2 = {"data_dir": str(tmp), "output_dir": str(tmp / "out")}
db2 = DB(str(tmp / "d.db"))
async def few(cfg): return [["AAAAA", "voco", "x"]] * 10
async def many(cfg): return [["AAAAA", "voco", "x"]] * 5000
async def boom(cfg): raise RuntimeError("x")
ihgdir.fetch = few
run.refresh_ihg_dir(c2, db2)
assert not ups and not db2.was_sent("ihg-dir")
ihgdir.fetch = boom
run.refresh_ihg_dir(c2, db2)
assert not ups and not db2.was_sent("ihg-dir")
ihgdir.fetch = many
run.refresh_ihg_dir(c2, db2)
assert ups == ["site/hotels-ihg.json"] and db2.was_sent("ihg-dir")
run.refresh_ihg_dir(c2, db2)
assert len(ups) == 1, ups
old = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d %H:%M")
db2.con.execute("UPDATE alerts_sent SET sent_at=? WHERE key='ihg-dir'", (old,)); db2.con.commit()
run.refresh_ihg_dir(c2, db2)
assert len(ups) == 2, ups
print("poll 測試全部通過")
