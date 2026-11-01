@echo off
chcp 65001 >nul
schtasks /Delete /F /TN "HotelMon Daily AM"
schtasks /Delete /F /TN "HotelMon Daily PM"
schtasks /Delete /F /TN "HotelMon Watch"
pause
