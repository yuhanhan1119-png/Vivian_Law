@echo off
chcp 65001 >nul
rem 雙擊這個檔案就會啟動「法規整理」App，並自動開啟瀏覽器。
rem 手機安裝：連上同一個 Wi-Fi，用手機開啟畫面上顯示的網址，選「加入主畫面」。

setlocal
set "SCRIPT_DIR=%~dp0"
if "%LAWKIT_HOME%"=="" set "LAWKIT_HOME=D:\Vivian_Law"

echo 資料夾：%LAWKIT_HOME%
echo 啟動中，請稍候...
echo.

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%SCRIPT_DIR%law.py" serve --port 8383
) else (
  python "%SCRIPT_DIR%law.py" serve --port 8383
)

echo.
echo App 已結束。按任意鍵關閉視窗。
pause >nul
endlocal
