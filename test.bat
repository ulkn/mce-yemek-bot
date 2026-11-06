@echo off
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python bot.py --test %*
if exist card_test.jpg start "" card_test.jpg
pause
