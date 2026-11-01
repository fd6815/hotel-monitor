@echo off
chcp 65001 >nul
cd /d "%~dp0"
set DIR=%cd%
rem 用 pythonw 背景執行，不跳視窗；log 寫到 data\daily.log、data\watch.log
schtasks /Create /F /TN "HotelMon Daily AM" /SC DAILY /ST 07:13 /TR "\"%DIR%\.venv\Scripts\pythonw.exe\" \"%DIR%\run.py\" daily"
schtasks /Create /F /TN "HotelMon Daily PM" /SC DAILY /ST 19:13 /TR "\"%DIR%\.venv\Scripts\pythonw.exe\" \"%DIR%\run.py\" daily"
schtasks /Create /F /TN "HotelMon Watch" /SC MINUTE /MO 30 /TR "\"%DIR%\.venv\Scripts\pythonw.exe\" \"%DIR%\run.py\" watch"
echo.
echo 已建立排程：每天 07:13、19:13 抓日曆；每 30 分鐘檢查監控。
echo 刪除排程請執行 remove_schedule.bat
pause
