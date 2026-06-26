@echo off
title AI Growth-Stock / ETF Monitor
cd /d "%~dp0"
echo ================================================================
echo    AI Growth-Stock / ETF Monitor  -  starting...
echo    A browser will open at  http://localhost:8501  (2nd tab = AI Pyramid)
echo    To STOP: press Ctrl+C here, or close this window.
echo ================================================================
echo.
REM use the py launcher (immune to the Windows Store python alias that breaks bare 'python')
py -c "import streamlit" 1>nul 2>nul
if errorlevel 1 py -m pip install -r requirements.txt
py -m streamlit run app.py
echo.
echo Dashboard stopped. Press any key to close.
pause >nul
