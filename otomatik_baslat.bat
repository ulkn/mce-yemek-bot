@echo off
cd /d "%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\YemekBotu.vbs" echo CreateObject("WScript.Shell").Run "wscript.exe ""%~dp0arka_planda_baslat.vbs""", 0, False
if exist "%STARTUP%\YemekBotu.vbs" (echo Bot artik Windows oturumu acildiginda otomatik baslayacak.) else (echo Baslangic dosyasi olusturulamadi.)
echo Kaldirmak icin su dosyayi sil: "%STARTUP%\YemekBotu.vbs"
pause
