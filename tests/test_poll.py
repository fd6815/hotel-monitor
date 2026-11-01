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
cfg = {"db_path": str(tmp / "t.db"), "data_dir": str(tmp), "days_ahead": 60, "github": {},
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
    c = {"browser": {"delay_seconds": [0, 0]}}
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
run.publish.publish = lambda *a, **k: calls.__setitem__("published", calls["published"] + 1)
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

# month 只補 60 天以後，不跑降價檢查；60 天內的 month 直接完成、不抓
calls["drops"] = 0
m = (date.today() + timedelta(days=120)).strftime("%Y-%m")
poll({"id": "r2", "type": "month", "hotel": "a", "month": m, "t": 0})
assert set(captured) == {"a"} and calls["drops"] == 0 and db.was_sent("req:r2"), captured
n = calls["collect"]
poll({"id": "r2b", "type": "month", "hotel": "a", "month": date.today().strftime("%Y-%m"), "t": 0})
assert calls["collect"] == n and db.was_sent("req:r2b"), calls

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
print("poll 測試全部通過")
