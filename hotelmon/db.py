"""SQLite 儲存：每日房價快照、已送出通知、已看過的優惠文章。"""
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(
  hotel      TEXT NOT NULL,
  stay_date  TEXT NOT NULL,          -- 入住日 YYYY-MM-DD
  fetch_day  TEXT NOT NULL,          -- 抓取日 YYYY-MM-DD（同一天重抓會覆蓋）
  price      INTEGER,                -- 積分或現金
  unit       TEXT,                   -- pts / TWD / JPY ...
  status     TEXT NOT NULL,          -- ok / sold_out / error
  note       TEXT,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(hotel, stay_date, fetch_day)
);
CREATE TABLE IF NOT EXISTS alerts_sent(key TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deals_seen(
  link TEXT PRIMARY KEY, title TEXT, source TEXT, published TEXT,
  matched INTEGER NOT NULL DEFAULT 0, seen_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(prices)")}
        if "cash" not in cols:
            self.con.execute("ALTER TABLE prices ADD COLUMN cash INTEGER")
            self.con.execute("ALTER TABLE prices ADD COLUMN cash_unit TEXT")
            self.con.commit()
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(deals_seen)")}
        if "title_zh" not in cols:
            self.con.execute("ALTER TABLE deals_seen ADD COLUMN title_zh TEXT")
            self.con.execute("ALTER TABLE deals_seen ADD COLUMN summary_zh TEXT")
            self.con.commit()

    # ---- 房價 ----
    def save_quote(self, q, fetch_day: str):
        self.con.execute(
            "INSERT OR REPLACE INTO prices(hotel,stay_date,fetch_day,price,unit,status,note,fetched_at,cash,cash_unit)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (q.hotel, q.stay_date.isoformat(), fetch_day, q.price, q.unit,
             q.status, q.note, now(), getattr(q, "cash", None), getattr(q, "cash_unit", None)),
        )
        self.con.commit()

    def snapshots(self, hotel: str, from_date: str) -> dict:
        """回傳 {stay_date: [row(最新), row(前一次), ...]}，依抓取日由新到舊。"""
        rows = self.con.execute(
            "SELECT * FROM prices WHERE hotel=? AND stay_date>=? AND status!='error' "
            "ORDER BY stay_date, fetch_day DESC",
            (hotel, from_date),
        ).fetchall()
        out: dict = {}
        for r in rows:
            out.setdefault(r["stay_date"], []).append(r)
        return out

    def last_fetch(self, hotel: str):
        r = self.con.execute(
            "SELECT MAX(fetched_at) AS t FROM prices WHERE hotel=? AND status!='error'", (hotel,)
        ).fetchone()
        return r["t"] if r else None

    # ---- 通知去重 ----
    def was_sent(self, key: str):
        r = self.con.execute("SELECT sent_at FROM alerts_sent WHERE key=?", (key,)).fetchone()
        return r["sent_at"] if r else None

    def mark_sent(self, key: str):
        self.con.execute("INSERT OR REPLACE INTO alerts_sent VALUES(?,?)", (key, now()))
        self.con.commit()

    def clear_sent(self, key: str):
        self.con.execute("DELETE FROM alerts_sent WHERE key=?", (key,))
        self.con.commit()

    def recent_keys(self, prefix: str, limit=60):
        return [r["key"] for r in self.con.execute(
            "SELECT key FROM alerts_sent WHERE key LIKE ? ORDER BY sent_at DESC LIMIT ?", (prefix + "%", limit))]

    # ---- 優惠 ----
    def deals_count(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM deals_seen").fetchone()[0]

    def deal_seen(self, link: str) -> bool:
        return self.con.execute("SELECT 1 FROM deals_seen WHERE link=?", (link,)).fetchone() is not None

    def add_deal(self, link, title, source, published, matched, title_zh=None, summary_zh=None):
        self.con.execute(
            "INSERT OR IGNORE INTO deals_seen(link,title,source,published,matched,seen_at,title_zh,summary_zh)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (link, title, source, published, int(matched), now(), title_zh, summary_zh),
        )
        self.con.commit()

    def all_deals(self):
        return self.con.execute("SELECT * FROM deals_seen").fetchall()

    def update_deal(self, link, matched, title_zh, summary_zh):
        self.con.execute("UPDATE deals_seen SET matched=?, title_zh=?, summary_zh=? WHERE link=?",
                         (int(matched), title_zh, summary_zh, link))
        self.con.commit()

    def recent_deals(self, limit=30):
        return self.con.execute(
            "SELECT * FROM deals_seen WHERE matched=1 ORDER BY seen_at DESC, published DESC LIMIT ?",
            (limit,),
        ).fetchall()
