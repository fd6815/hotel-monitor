# 飯店房價監控（仿早早）

每日積分／現金房價日曆、指定日期門檻監控、收藏降價與新開通知、優惠 RSS 情報。
全部由 Python 程式定時執行，不使用 AI。

## 架構（目前使用方式）
- 抓價在你的 Windows 電腦執行（家用網路，官網不會擋）：萬豪用一般 Chrome 開官網房價頁，IHG 直接呼叫官網 API。
- 抓完把日曆頁上傳到本 repo 的 `site/index.html`，GitHub Actions 自動發布到 https://fd6815.github.io/hotel-monitor/ 給手機看。
- 上傳需要 GitHub token，放在電腦的 `data\github_token.txt`（不會上傳到 repo）。
- 雲端（GitHub Actions）直接抓萬豪會被防火牆擋（Access Denied），所以不在雲端抓價。

## 功能
| 功能 | 指令 | 建議頻率 |
|---|---|---|
| 每日房價日曆（未來 60 天，漲降與前次比較） | `run.py daily` | 每天 1–2 次 |
| 指定日期監控（低於門檻通知一次 → 已完成） | `run.py watch` | 每 30–60 分鐘 |
| 收藏飯店降價、搶空轉有房（新開） | 含在 `daily` | — |
| 優惠情報 RSS（關鍵字過濾） | 含在 `daily`／`watch`，或 `run.py deals` | — |
| 日曆頁 `dist/index.html` | 自動產生，或 `run.py report` | — |

支援萬豪、IHG、凱悅、雅高（積分或現金），透過 Playwright 開官網訂房頁、以規則解析頁面最低價格。

## Windows 安裝
1. 雙擊 `install.bat`（自動建 venv、裝套件、跑模擬測試並開啟日曆頁）
2. 編輯 `config.yaml`
3. `login.bat` 登入會員 → `run_daily.bat` 試抓
4. `setup_schedule.bat` 建立 Windows 工作排程器（每天 07:13／19:13 日曆、每 30 分鐘監控），`remove_schedule.bat` 移除
5. 紀錄在 `data\daily.log`、`data\watch.log`

## macOS／Linux 安裝
```bash
cd hotel-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # 若 config 用 channel: chrome 則可略
cp config.example.yaml config.yaml   # 填飯店代碼、監控、Telegram
python run.py login                  # 開瀏覽器登入各會員（會員價／點數房需登入）
python run.py daily                  # 首次測試，開 dist/index.html 看結果
```

先不連網試看看：`python run.py daily -c config.demo.yaml` 用模擬資料產生 `dist-demo/index.html`。

## 設定重點
- **飯店代碼**：在官網打開該飯店訂房頁，從網址取得（萬豪 `propertyCode`、IHG `qSlH`、凱悅 `/shop/rooms/<代碼>`、雅高 `/rates/<代碼>/`）。
- **通知**：Telegram（`bot_token`＋`chat_id`）或 ntfy（手機裝 ntfy App 訂閱 topic）。
- **抓不到價格時**：看 `data/debug/*.png` 截圖。可在該飯店加 `url_template`（自訂網址）、`price_selector`（限定 CSS 區塊）、`ignore_values`（排除你的點數餘額）。
- **速度**：每次查詢間隔 4–9 秒，60 天 × 4 間約 30 分鐘。飯店多時可把 `days_ahead` 調小。

## 排程
- Linux／一般 cron：`scripts/crontab.example`
- macOS：`scripts/com.hotelmon.daily.plist`、`com.hotelmon.watch.plist`，改路徑後放到 `~/Library/LaunchAgents/` 並 `launchctl load`。

建議跑在家裡常開的電腦或 NAS。雲端主機（含 GitHub Actions）的 IP 常被飯店官網直接擋掉。

## 手機看日曆
`dist/index.html` 是單一檔案、資料內嵌。可用 `python -m http.server -d dist 8080` 在家中網路開，
或在排程後加一行把 `dist/` 推到 GitHub Pages／Cloudflare Pages。

## 限制
- 官網改版或防爬升級時解析會失效，程式會發「抓取失敗」通知；需調整網址範本或 selector。
- 頻率請保守，過度查詢可能觸發會員帳號風控。
- 未做積分價值（現金價 ÷ 點數）計算；如需可在 hotels 再加一筆同飯店的 `mode: cash`。
