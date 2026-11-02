# 飯店房價監控（仿早早）

積分／現金房價日曆、指定日期門檻監控、收藏降價與新開通知、優惠情報。全部由程式定時執行，不使用 AI。
手機網頁：https://fd6815.github.io/hotel-monitor/

## 資料來源
| 集團 | 來源 | 頻率 |
|---|---|---|
| IHG | 手機網頁直接查官網 API；電腦用 Chrome 查監控日期 | 打開頁面時；電腦每 30 分鐘，週日完整 60 天 |
| 萬豪（監控日期） | 官網月曆 API，只查點數 | 每個日期每天一次 |
| 萬豪（其他日期與現金） | 第三方 [ustatz.com](https://www.ustatz.com/hotelGroups/)（PY的旅行大小事），不一定最新，訂房前請到官網確認 | 每間每天一次 |
| 優惠 | RSS，關鍵字篩選；Google 翻譯免費端點翻成中文 | 每 30 分鐘 |

雲端（GitHub Actions）的 IP 會被官網擋，所以抓價在 Windows 電腦上執行，資料上傳到 repo 的 `site/data.json`。

## 指令
```
python run.py daily      週日完整 60 天，其他時間只查監控日期（--full 強制完整）
python run.py watch      只查監控日期
python run.py poll       處理手機網頁送來的請求
python run.py report     重產並上傳 data.json
python run.py deals      只檢查優惠
python run.py ihg-dir    立即更新 IHG 飯店清單（平常由 daily 每 30 天更新）
python run.py login      開瀏覽器登入會員
python run.py test-line  測試 LINE 通知
```

## 安裝（Windows）
1. `install.bat`：建 venv、裝套件。
2. 複製 `config.example.yaml` 成 `config.yaml`，放好 `data\github_token.txt`、`data\line.json`。
3. `setup_schedule.bat`：建立工作排程（背景執行，不跳視窗），`remove_schedule.bat` 移除。Poll 排程會自動建立。

紀錄在 `data\daily.log`、`data\watch.log`、`data\poll.log`。

## 開發
- 架構與規則：`CLAUDE.md`；API 與計算細節：`docs/scraping.md`。
- 測試（不連官網）：`.venv\Scripts\python.exe tests\test_poll.py`
- 手機網頁是手寫的 `site/index.html`，push 後由 GitHub Pages 部署。
