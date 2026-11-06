@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (.venv\Scripts\python baglanti_testi.py) else (python baglanti_testi.py)
pause
