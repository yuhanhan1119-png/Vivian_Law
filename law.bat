@echo off
rem 法規整理工具（Windows 命令列進入點）
rem 用法：law import D:\Vivian_Law\sources\民法.txt
rem       law search 損害賠償
rem       law serve

setlocal
set "SCRIPT_DIR=%~dp0"

rem 未設定時，資料預設整理在 D:\Vivian_Law
if "%LAWKIT_HOME%"=="" set "LAWKIT_HOME=D:\Vivian_Law"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%SCRIPT_DIR%law.py" %*
) else (
  python "%SCRIPT_DIR%law.py" %*
)
endlocal
