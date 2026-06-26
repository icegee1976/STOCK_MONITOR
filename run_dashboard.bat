@echo off
title AI Growth-Stock / ETF Monitor
cd /d "%~dp0"
echo ================================================================
echo    AI Growth-Stock / ETF Monitor  -  starting...
echo ----------------------------------------------------------------
echo    A browser will open at  http://localhost:8501
echo    ( 2nd tab = AI Pyramid view )
echo    To STOP: press Ctrl+C here, or just close this window.
echo ================================================================
echo.
python -c "import streamlit" 1>nul 2>nul
if errorlevel 1 python -m pip install -r requirements.txt
python -m streamlit run app.py
echo.
echo Dashboard stopped. Press any key to close.
pause >nul
