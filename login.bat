@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist data mkdir data
.venv\Scripts\python run.py login %*
