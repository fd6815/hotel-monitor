@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo [1/5] 檢查 Python...
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo 找不到 Python，嘗試用 winget 安裝 Python 3.12...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo 安裝完成後請關閉此視窗，重新執行 install.bat
  pause
  exit /b 1
)
echo [2/5] 建立虛擬環境 .venv...
if not exist .venv ( %PY% -m venv .venv || goto :err )
echo [3/5] 安裝套件...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt || goto :err
echo [4/5] 安裝 Playwright Chromium（備用瀏覽器）...
.venv\Scripts\python -m playwright install chromium || goto :err
if not exist config.yaml copy config.example.yaml config.yaml >nul
echo [5/5] 用模擬資料測試...
.venv\Scripts\python run.py daily -c config.demo.yaml > data_demo_test.log 2>&1 || goto :err
del data_demo_test.log
start "" "dist-demo\index.html"
echo.
echo 安裝完成，已開啟模擬日曆頁。
echo 下一步：編輯 config.yaml，然後執行 login.bat 登入會員、run_daily.bat 試抓。
pause
exit /b 0
:err
echo 安裝失敗，請把上方訊息截圖回報。
if exist data_demo_test.log type data_demo_test.log
pause
exit /b 1
