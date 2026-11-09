# 飯店房價監控（仿微信小程式「早早」）

使用者：繁體中文、台灣用語、回覆精簡。Windows，專案在 `C:\Users\fd681\Documents\hotel-monitor`。
GitHub：`fd6815/hotel-monitor`（公開）。手機網頁：https://fd6815.github.io/hotel-monitor/
原則：資料更新一律由程式執行，不用 AI、不耗 Claude token。

## 架構
- **電腦**（家用網路）：`run.py daily/watch/poll` 由工作排程器執行，用 Chrome（CDP）在官網頁面內呼叫 API → SQLite `data/hotelmon.db` → LINE 通知 → `dist/data.json` 用 GitHub contents API 傳到 `site/data.json`。
- **手機網頁** `site/index.html`（手寫靜態頁）：讀 data.json。IHG 由網頁直接查官網 API，結果存 localStorage `hm-live`（只在該瀏覽器）；超過 3 天用月曆 API 一次查一個月（只有點數，現金沿用前次），失敗才逐日查；查詢排隊一次一個、間隔 1–2 秒，每天最多 150 次（`hm-ihg-calls:<日>`），403／429 就停 2 小時（`hm-ihg-block`，data.json 的 `blocked[].until` 也算），被擋或達上限不轉請電腦查；「立即完整更新」只查目前打開的 IHG 飯店。萬豪寫請求到 `watch/requests.json` 由電腦 poll 處理。寫入 GitHub 用手機 localStorage 的 `hm-token`。
- `watch/watchlist.json`：收藏與監控（手機讀寫，電腦每次執行前讀）。`site/hotels-marriott.json`：萬豪清單（directory.yml 每週重建）。`site/hotels-ihg.json`：IHG 清單（`[[代碼, 品牌, 城市]]`，daily 每 30 天由電腦 Chrome 讀官網 sitemap 更新，`run.py ihg-dir` 可手動更新）。pages.yml：`site/**` 變動時部署。
- GitHub Actions 雲端 IP 被萬豪、IHG 官網擋，電腦和手機（非雲端）才能抓。

## 優惠情報（`hotelmon/deals.py`，關鍵字規則，不用 AI）
每 30 分鐘隨 Watch 讀 RSS（config.yaml 的 `deals.feeds`）。網頁只留特價與優惠技巧；LINE 只傳 IHG／Avios／Alaska 點數特賣、bug 價、快速會籍匹配。翻譯用 Google 翻譯免費端點（非官方，失敗顯示原文）。

## 監控（watchlist.json `monitors`）
單日 `{hotel, date, max_price}`：低於門檻通知一次就完成（萬豪 ustatz 來源例外，價格變了再通知）。區間多一個 `end`（含，最長 92 天，`watchlist.mon_dates`）：期間內每個低於門檻的日期各通知一次（鍵 `mon:<飯店>:<起>~<迄>:<門檻>:<日期>`），到期才結束；查價範圍、萬豪官網、手機開頁都會展開成每一天。`kind: "cash"` 是現金門檻（台幣，`alerts.mon_value` 用 fx 換算；萬豪含稅、來自 ustatz，所以不觸發官網、價格變了再通知；IHG 未稅，現金監控日期在 collect 逐日查最近 `IHG_CASH_MAX`＝10 天，其餘用月曆）。手機日曆點日期時，依上方點數／現金切換決定加哪一種。data.json 的 monitors 有 `end`、`cur_date`（區間最低那天）、`src`（官網／ustatz）、`t`（查詢時間）、`hits`。手機「我的監控」可新增區間監控、點一筆改門檻。網頁可加到主畫面（`site/manifest.webmanifest`，圖示由 `scripts/make_icons.py` 產生）。

## 一年內新低通知（`alerts.check_year_low`）
收藏飯店（點數模式）未來 365 天的最低點數存在 db `counters` 表 `low:<飯店>`。資料要涵蓋到 300 天後才算；第一次只記錄不通知；之後比紀錄更低才傳 LINE「一年內新低」並更新紀錄（同價、最低那天被訂走都不通知）。daily／watch／poll 每次都檢查。

## 萬豪目前做法
資料來源是第三方 ustatz.com（`hotelmon/ustatz.py`，一般 requests 可讀）：每間飯店每天 07:00 後讀一次，一次約 11 個月的點數與現金（含稅，IHG 是未稅），對方是預先抓好的，不一定最新。失敗照封鎖間隔重試。手機請求和切換月份都不觸發萬豪。監控日期另外每 8 小時用官網月曆 API 查一次（`update_marriott_official`，輪次從 07、15、23 點起算，同一輪每個日期只查一次；鍵 `maroff:<日>:<輪>:<飯店>:<日期>`）。失敗就傳 LINE、沿用 ustatz，下一輪再試；被封鎖則到下一個 07:00 前都不查（鍵 `maroff-blocked:<日>`，「日」從 07:00 算起）。只查點數，現金沿用 ustatz，`note` 記為「官網」。到價通知：官網來源通知一次就完成；ustatz 來源不結束監控，價格變了才再通知（鍵 `mon:...:<價格>`）。整段官網查價和逐日開頁的程式還留著（config `marriott_api: true` 才用）。

## 排程（pythonw 背景執行，需開機且登入，`data\run.lock` 防重複，90 分鐘逾時）
`data.json` 沒實質變動就不上傳（`publish_data`），但每 3 小時（`heartbeat_hours`）至少傳一次當心跳；手機網頁超過 4 小時（`PC_STALE_H`）沒更新就把右上角改成紅字「電腦 X 小時沒更新」並跳提示。GitHub token 7 天內到期或失效會傳 LINE。
| 名稱 | 時間 | 內容 | log |
|---|---|---|---|
| HotelMon Daily AM/PM | 07:13、19:13 | 週日第一次跑完整 60 天（`--full` 強制），其他時間等同 Watch | `data\daily.log` |
| HotelMon Watch | :13、:43 | 只查監控日期（IHG 每 `ihg_watch_minutes`＝60 分鐘才查，鍵 `ihg-watch`）；沒資料的新飯店抓 60 天 | `data\watch.log` |

電腦查 IHG：超過 3 天（`IHG_RANGE_MIN`）用月曆 API（`BrowserFetcher.ihg_range`，一次 31 天、只有點數、現金沿用前次，失敗改逐日）；監控日期仍逐日查（有現金）。IHG 未來一年每天 07:00 後由第一次 watch／daily 用月曆查一次（`ihg_year_due`，鍵 `ihg-year:<日>`，每間約 12 次請求，失敗下一次再試）。間隔 `browser.ihg_delay_seconds`（3–6 秒），每天最多 `ihg_daily_cap`＝150 次（db `counters` 表 `calls:ihg:<日>`）。任一飯店被擋（`alerts.is_denied`：Access Denied／HTTP 403／429）就停掉這次同集團全部查詢，並算失敗、進入封鎖等待。pythonw 的 log 每行開頭有時間。
| HotelMon Poll | 每 1 分 | 處理手機請求，沒請求就安靜結束 | `data\poll.log` |

## 主要檔案
- `run.py`（daily／watch／poll／report／login／test-line）、`hotelmon/fetchers.py`（抓價）、`report.py`（data.json）、`alerts.py`（通知、封鎖狀態）、`watchlist.py`、`publish.py`、`notify.py`、`deals.py`、`db.py`、`fx.py`
- `tests/test_poll.py`：不連官網的測試。
- `docs/scraping.md`：API 細節、計算規則、請求格式。**改抓價或請求邏輯時才讀。**
- 不用讀：`site/hotels-marriott.json`、`site/hotels-ihg.json`、`site/data.json`（大檔）、`hotelmon/template.html`（只給 demo）、`data\archive\`（舊檔）。
- 機密（只在電腦、不進 repo）：`data\github_token.txt`（2026-12-01 到期）、`data\line.json`。

## 開發規則
- **改完先跑測試，不要碰真實官網**：`.venv\Scripts\python.exe tests\test_poll.py`。萬豪查太快會被封鎖 1–2 小時，真的要測才查，一次就好。
- 網頁只改 `site/index.html` 需要的部分，用 Grep 找位置，不要整份讀。
- 部署：電腦程式改完下一次排程就生效；手機網頁 push 後由 pages.yml 部署。git 在 `C:\Program Files\Git\cmd\git.exe`（新開的 shell 才有 PATH），credential helper（`scripts/git-credential.sh`）會讀 `data\github_token.txt`。
- 電腦和手機會持續 commit 到 main，push 前先 `git pull --rebase`。改 `.github/workflows` 需要 token 有 Workflows 權限，目前沒有。

## 待辦
1. 萬豪官網 11-04～11-09 每天第一次請求就被擋，原因是電腦開著 NordVPN（對外 IP 是 PacketHub 機房 IP，Akamai 會擋查價 API）；2026-11-09 21:30 關 VPN 後（中華電信 IP）月曆 API 回 200。**抓價時電腦和手機都不要開 VPN**（電腦：`hotelmon/vpn.py` 在 BrowserFetcher 開瀏覽器前偵測 NordLynx/NordVPN 網卡，連線中只寫 log；NordVPN 8.15 命令列斷線無效，程式關不掉）；再被擋先用 `curl -s https://ipinfo.io/json` 看 org 是不是 VPN。其他日期仍用 ustatz，ustatz 改版或停站時 `ustatz.fetch` 會失敗並傳 LINE。
6. 觀察萬豪官網 2026-11-10～11-12（不開 VPN）有沒有被擋（看 `maroff-blocked:` 鍵或 daily/watch log）；都沒被擋就把官網監控從每 8 小時改成每 4 小時（使用者已同意）。
5. 優惠來源「里程家」找不到網站，等使用者提供網址。
2. 2026-11-02 00:22 的 poll 查台北洲際時瀏覽器中途關閉（Target page, context or browser has been closed），原因未查明，再發生要查。
4. 目前收藏：名古屋萬豪 NGODT、台北洲際 TPEPI；監控：名古屋萬豪 2026-11-28 < 60000、台北洲際 2026-12-06 < 64000。
