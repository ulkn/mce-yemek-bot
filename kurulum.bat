@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Yemek Botu kurulumu ===
where python >nul 2>nul || (echo Python bulunamadi. Python 3.9 veya ustu gerekli. & pause & exit /b 1)
python --version
rem Baska bilgisayardan kopyalanan .venv bu bilgisayarin Python'una uymaz: kontrol et, uymuyorsa sil
if exist .venv (
  python -c "import sys,re,pathlib;t=pathlib.Path('.venv/pyvenv.cfg').read_text(errors='ignore');m=re.search(r'version(?:_info)?\s*=\s*(\d+\.\d+)',t);v=str(sys.version_info[0])+'.'+str(sys.version_info[1]);sys.exit(0 if m and m.group(1)==v else 1)" 2>nul
  if errorlevel 1 (
    echo Eski sanal ortam bu bilgisayara uymuyor, yeniden olusturuluyor...
    rmdir /s /q .venv
  ) else (
    .venv\Scripts\python.exe -c "import sys" >nul 2>nul || (echo Sanal ortam bozuk, yeniden olusturuluyor... & rmdir /s /q .venv)
  )
)
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install --prefer-binary -r requirements.txt || (echo. & echo pip kurulumu basarisiz. Proxy gerekiyorsa README dosyasina bak. & pause & exit /b 1)
if not exist config.json copy config.example.json config.json >nul
echo.
echo Kurulum tamam. Simdi:
echo  1) baglanti_testi.bat ile hangi modun kullanilacagini ogren
echo  2) README'ye gore Worker'i kur ve config.json'u doldur
echo  3) giris_yap.bat, test.bat, sonra baslat.bat
pause
