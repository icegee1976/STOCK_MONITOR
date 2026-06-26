@echo off
chcp 65001 >nul
title AI 成長股 / ETF 監測儀表板
cd /d "%~dp0"
echo ================================================================
echo     AI 成長股 / ETF 監測儀表板    啟動中...
echo ----------------------------------------------------------------
echo     稍候瀏覽器會自動開啟   http://localhost:8501
echo     第 2 個分頁 = AI 金字塔分層檢視
echo     要關閉:在此視窗按 Ctrl + C,或直接關掉這個黑視窗
echo ================================================================
echo.
python -c "import streamlit" 1>nul 2>nul
if errorlevel 1 (echo 第一次執行,安裝相依套件中... & python -m pip install -r requirements.txt)
python -m streamlit run app.py
echo.
echo 儀表板已停止。按任意鍵關閉視窗。
pause >nul
