@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Yemek Botu kurulumu ===
where python >nul 2>nul || (echo Python bulunamadi. python.org veya Sirket Portali uzerinden Python 3.9+ kur. & pause & exit /b 1)
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt || (echo. & echo pip kurulumu basarisiz. Proxy gerekiyorsa README dosyasina bak. & pause & exit /b 1)
if not exist config.json copy config.example.json config.json >nul
echo.
echo Kurulum tamam. Simdi:
echo  1) baglanti_testi.bat ile hangi modun kullanilacagini ogren
echo  2) README'ye gore Worker'i kur ve config.json'u doldur
echo  3) giris_yap.bat, test.bat, sonra baslat.bat
pause
