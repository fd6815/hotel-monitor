# 飯店房價監控（仿微信小程式「早早」）

使用者：繁體中文、台灣用語、回覆精簡。Windows，專案在 `C:\Users\fd681\Documents\hotel-monitor`。
GitHub：`fd6815/hotel-monitor`（公開）。手機網頁：https://fd6815.github.io/hotel-monitor/
原則：資料更新一律由程式執行，不用 AI、不耗 Claude token。

## 架構
- **電腦**（家用網路）：`run.py daily/watch/poll` 由工作排程器執行，用 Chrome（CDP）在官網頁面內呼叫 API → SQLite `data/hotelmon.db` → LINE 通知 → `dist/data.json` 用 GitHub contents API 傳到 `site/data.json`。
- **手機網頁** `site/index.html`（手寫靜態頁）：讀 data.json。IHG 由網頁直接查官網 API，結果存 localStorage `hm-live`（只在該瀏覽器）；萬豪寫請求到 `watch/requests.json` 由電腦 poll 處理。寫入 GitHub 用手機 localStorage 的 `hm-token`。
- `watch/watchlist.json`：收藏與監控（手機讀寫，電腦每次執行前讀）。`site/hotels-marriott.json`：萬豪清單（directory.yml 每週重建）。pages.yml：`site/**` 變動時部署。
- GitHub Actions 雲端 IP 被萬豪、IHG 官網擋，電腦和手機（非雲端）才能抓。

## 排程（需開機且登入，`data\run.lock` 防重複，90 分鐘逾時）
| 名稱 | 時間 | 內容 | log |
|---|---|---|---|
| HotelMon Daily AM/PM | 07:13、19:13 | 收藏飯店未來 60 天＋監控日期 | `data\daily.log` |
| HotelMon Watch | :13、:43 | 只查監控日期；沒資料的新飯店抓 60 天 | `data\watch.log` |
| HotelMon Poll | 每 1 分 | 處理手機請求，沒請求就安靜結束 | `data\poll.log` |

## 主要檔案
- `run.py`（daily／watch／poll／report／login／test-line）、`hotelmon/fetchers.py`（抓價）、`report.py`（data.json）、`alerts.py`（通知、封鎖狀態）、`watchlist.py`、`publish.py`、`notify.py`、`deals.py`、`db.py`、`fx.py`
- `tests/test_poll.py`：不連官網的測試。
- `docs/scraping.md`：API 細節、計算規則、請求格式。**改抓價或請求邏輯時才讀。**
- 不用讀：`site/hotels-marriott.json`、`site/data.json`（大檔）、`hotelmon/template.html`（只給 demo）、`data\archive\`（舊檔）。
- 機密（只在電腦、不進 repo）：`data\github_token.txt`（2026-12-01 到期）、`data\line.json`。

## 開發規則
- **改完先跑測試，不要碰真實官網**：`.venv\Scripts\python.exe tests\test_poll.py`。萬豪查太快會被封鎖 1–2 小時，真的要測才查，一次就好。
- 網頁只改 `site/index.html` 需要的部分，用 Grep 找位置，不要整份讀。
- 部署：電腦程式改完下一次排程就生效；手機網頁 push 後由 pages.yml 部署。git 在 `C:\Program Files\Git\cmd\git.exe`（新開的 shell 才有 PATH），credential helper（`scripts/git-credential.sh`）會讀 `data\github_token.txt`。
- 電腦和手機會持續 commit 到 main，push 前先 `git pull --rebase`。改 `.github/workflows` 需要 token 有 Workflows 權限，目前沒有。

## 待辦
1. 萬豪解封後驗證：月曆 API 速度與點數、STANDARD 現金（不行就抓官網現金月曆頁 GraphQL）、網頁能否直接查（跨網域）。
2. 2026-11-02 00:22 的 poll 查台北洲際時瀏覽器中途關閉（Target page, context or browser has been closed），原因未查明，再發生要查。
3. 監控門檻目前只有點數，可考慮加現金門檻。
4. IHG 飯店清單尚未建立，目前只能用代碼或網址新增。
5. 目前收藏：名古屋萬豪 NGODT、台北洲際 TPEPI；監控：名古屋萬豪 2026-11-28 < 60000、台北洲際 2026-12-06 < 64000。
