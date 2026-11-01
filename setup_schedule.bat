@echo off
chcp 65001 >nul
cd /d "%~dp0"
set DIR=%cd%
schtasks /Create /F /TN "HotelMon Daily AM" /SC DAILY /ST 07:13 /TR "\"%DIR%\scripts\task.bat\" daily"
schtasks /Create /F /TN "HotelMon Daily PM" /SC DAILY /ST 19:13 /TR "\"%DIR%\scripts\task.bat\" daily"
schtasks /Create /F /TN "HotelMon Watch" /SC MINUTE /MO 30 /TR "\"%DIR%\scripts\task.bat\" watch"
echo.
echo 已建立排程：每天 07:13、19:13 抓日曆；每 30 分鐘檢查監控。
echo 刪除排程請執行 remove_schedule.bat
pause
