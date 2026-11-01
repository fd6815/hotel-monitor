# 飯店房價監控（仿微信小程式「早早」）— 專案交接

使用者：繁體中文、台灣用語、回覆精簡。Windows 電腦，專案在 `C:\Users\fd681\Documents\hotel-monitor`。
GitHub：`fd6815/hotel-monitor`（公開）。手機網頁：https://fd6815.github.io/hotel-monitor/
原則：資料更新一律由程式定期執行，不用 AI、不耗 Claude token。

## 架構
```
你的 Windows 電腦（家用網路，官網不擋）
  run.py daily / watch / poll  ← Windows 工作排程器
  ├─ 萬豪：一般 Chrome（CDP 連線，非自動化模式）開 marriott.com，在頁面內呼叫價格月曆 GraphQL API
  ├─ IHG：在 ihg.com 頁面內呼叫 apis.ihg.com availability API
  ├─ SQLite data/hotelmon.db（每日快照、通知去重、請求完成紀錄）
  ├─ 通知：LINE Messaging API（broadcast）
  └─ 產生 dist/data.json → GitHub contents API 上傳到 site/data.json
GitHub
  ├─ site/index.html          手機網頁（靜態、手寫，不由程式產生）
  ├─ site/data.json           電腦上傳的房價資料
  ├─ site/hotels-marriott.json 萬豪飯店清單（約 1 萬間，.github/workflows/directory.yml 每週從 sitemap 重建）
  ├─ watch/watchlist.json     收藏飯店與監控（手機網頁用 GitHub API 讀寫，電腦每次執行前讀取）
  ├─ watch/requests.json      手機送出的即時更新請求
  └─ .github/workflows/pages.yml  site/** 變動時部署 GitHub Pages
```
GitHub Actions 雲端直接抓萬豪會被 Akamai 擋（Access Denied），IHG 官網也擋雲端 IP，所以抓價只在使用者電腦執行。

## 排程（Windows 工作排程器，需開機且登入）
| 名稱 | 時間 | 指令 |
|---|---|---|
| HotelMon Daily AM / PM | 07:13、19:13 | `scripts\task.bat daily` → 收藏飯店未來 60 天＋監控日期，log `data\daily.log` |
| HotelMon Watch | 每 30 分（:13、:43） | `scripts\task.bat watch` → 只查監控日期；新收藏還沒資料的飯店抓完整 60 天，log `data\watch.log` |
| HotelMon Poll | 每 1 分 | `.venv\Scripts\pythonw.exe run.py poll`（由 run.py `ensure_poll_task()` 在 daily/watch 結束後自動建立）→ 處理 watch/requests.json，log `data\poll.log`，沒請求就安靜結束 |
`data\run.lock` 防止同時執行（90 分鐘逾時）。

## 抓價細節
- **萬豪（主要）**：`BrowserFetcher.marriott_range()`。先開 `https://www.marriott.com/default.mi`，再在頁面內 POST `/mi/query/phoenixShopADFSearchProductsByProperty`。
  - 必須帶官網原始 query 文字（`hotelmon/marriott_calendar.graphql`）與 header `graphql-operation-signature: 887375892e1ad2a43f46a9c95c55ea47cf6eca3af03331c2134f1b440cff3f9f`、`graphql-require-safelisting: true`、`apollographql-client-name: phoenix_shop`。
  - 一次最多 35 天；`rateRequestTypes:[{type:"REDEMPTION"}]` 回傳 `pointsPerQuantity.points`；沒出現在 edges 的日期＝無積分房。
  - 現金用 `type:"STANDARD"` 讀 `lowestAverageRate.amount`（除以 10^decimalPoint）— **尚未實測成功**，失敗會設 `mar_cash_broken` 跳過。
  - 月曆 API 失敗時退回逐日開 `search/availabilityCalendar.mi?...flexibleDateSearch=false`（會轉到 rateListMenu.mi，解析 “N Points / Stay”），連續 3 次 Access Denied 就停止本次。
  - 萬豪查太快會被暫時封鎖約 1–2 小時（2026-11-01 逐日抓 33 天後被擋）。
- **IHG**：`IHG_JS` 在 ihg.com 頁面 fetch `https://apis.ihg.com/availability/v3/hotels/offers?fieldset=rateDetails`，header `x-ihg-api-key: hsL8GAAz7drdIbEjBCejdA1Ud2MVhxoo`（官網前端公開金鑰）。積分＝`ratePlanCode=="IVANI"` 的 `rewardNights.pointsOnly.totalPoints`；現金＝其他 offer 的 `totalRate.amountBeforeTax` 最低值。HTTP 400 + "los" ＝最少住宿天數限制。
- 標準房搶空只剩高階房：report 以中位數 1.7 倍判定為 `premium`，網頁顯示「搶空」。
- 匯率：`hotelmon/fx.py`（open.er-api.com，每日快取 data/fx.json）。
- 「每萬點價值」＝台幣現金 ÷ 點數 × 10000；排該飯店前 25% 為「划算」（粗體）。

## 手機網頁功能（site/index.html）
- 最上方切換：點數／現金／點數＋現金（localStorage hm-pmode）。
- 搜尋收藏：中文城市／品牌別名表 `ALIAS`＋萬豪清單；IHG 輸入 5 碼代碼或貼 ihg.com 網址。
- 日曆：一次一個月，‹ › 最遠 12 個月。60 天以後的月份停留 1.5 秒才送 `type:"month"` 請求（電腦只補 60 天以後、過期超過一天的日期）。
- 開頁送 `type:"open"` 請求（15 分鐘內不重複）：電腦查所有監控日期＋目前飯店當月。
- 點日期：看點數／現金／每萬點價值、加入／修改／刪除監控（門檻為點數）。
- 寫入需 GitHub token，存在手機瀏覽器 localStorage `hm-token`。
- 請求完成判斷：data.json 的 `requests_done` 含請求 id，網頁每 20 秒輪詢。

## 機密檔（只在電腦，不進 repo）
- `data\github_token.txt`：fine-grained token，只限 hotel-monitor repo，Contents read/write（2026-12-01 到期）。
- `data\line.json`：LINE channel access token（官方帳號 @612yhxqc，用 broadcast 推播，使用者是唯一好友）。

## 主要檔案
- `run.py`：daily / watch / poll / report / login / test-line
- `hotelmon/fetchers.py`：BrowserFetcher（萬豪月曆 API、逐日備援、IHG API）、DemoFetcher
- `hotelmon/report.py`：產生 data.json（premium、現金、價值、划算、requests_done）
- `hotelmon/alerts.py`：監控達標（通知一次後已完成）、降價／新開、抓取失敗
- `hotelmon/watchlist.py`、`publish.py`、`notify.py`、`deals.py`（RSS 優惠）、`db.py`、`fx.py`
- `config.yaml`：瀏覽器、github、deals 設定（hotels/monitors 以 GitHub watchlist 為準）
- `probe*.py`：當初逆向 API 用的診斷腳本，可刪

## 待辦／待確認（2026-11-02 00:12）
1. 00:13 排程是第一次用萬豪月曆 API 跑：確認速度、點數正確、STANDARD 現金是否可用，並確認 HotelMon Poll 已建立、手機請求有被處理。
2. 使用者想要「立即完整更新」：新增 request type `daily`（poll 收到就跑完整 60 天），網頁設定加按鈕。
3. 萬豪現金若 STANDARD 不行：抓官網現金月曆頁的 GraphQL（之前 probe5 flex_cash 被暫時封鎖沒抓到）。
4. 監控門檻目前只有點數，可考慮加現金門檻。
5. IHG 飯店清單尚未建立（ihg.com 擋雲端），目前只能用代碼或網址新增。
6. 目前收藏：名古屋萬豪 NGODT、台北洲際 TPEPI；監控：名古屋萬豪 2026-11-28 < 60000、台北洲際 2026-12-06 < 64000。

## 修改後部署
- 電腦程式：直接改這個資料夾的檔案即可，下一次排程生效。
- 手機網頁：改 `site/index.html` 後 push 到 GitHub（pages.yml 自動部署）。repo 裡也有一份 Python 程式碼，修改後請同步 push。
