@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Yemek Menusu Botu
.venv\Scripts\python bot.py
pause
