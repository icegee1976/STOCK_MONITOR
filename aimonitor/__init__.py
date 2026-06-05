"""AI 護國群山 — 美股 + 台股 AI/太空成長股價格帶監測引擎。

模組總覽:
    providers  資料來源 (台股 FinMind / 美股 yfinance) + 快取
    valuation  價格帶計算 (孫慶龍 forward-EPS × 本益比河流圖)
    classify   價位分類、距便宜價缺口、波動率觸及機率
    roi        情境式投報率試算 (含費用/稅/匯率)
    screener   在清單中篩出「目前便宜」的標的
    report     終端機報表輸出
"""

__version__ = "0.1.0"
