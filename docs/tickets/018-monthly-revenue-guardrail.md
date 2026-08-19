# 018 — 月營收假設健全度護欄(候選 D;**待人類拍板後才實作**)

狀態:OPEN(待拍板;C=017 收斂後提請決策)

## 構想
估價命脈是 `derive` 的營收假設(base_revenue × (1+CAGR)^年數)。接 FinMind
台股月營收(TaiwanStockMonthRevenue),把**實際營收軌跡 vs 假設**做成護欄警告
——與現有「隱含 P/E 護欄」同哲學:假設驗證,不是預測。**絕不自動改
watchlist.yaml(紅線 3),只警告。**

## 待拍板的財務語意(實作前需人類決定)

1. **比較法**(擇一或並用):
   - A|TTM 絕對值 vs 推導軌跡:近 12 月實際營收 vs `base_revenue×(1+CAGR)^經過年數`
     的期望值,偏離 % 直接對應估價鏈前提。
   - B|成長率:近 12 月 YoY vs 假設 CAGR。
   - C|僅並列顯示,不設判斷門檻。
   - **orchestrator 建議:A+B 並示、以 A 設警告門檻**(A 是估價鏈的直接前提;
     B 輔助判讀動能)。
2. **警告門檻**:A 落後多少 % 才警告?(建議 15%;過敏會狼來了,過鈍失去意義。)
3. **警告文案方向**(中性、非建議,例):「近 12 月實際營收較假設軌跡低 X%;
   forward EPS 假設可能過高,便宜價可能因此偏高,請檢視 watchlist 假設。」
4. **範圍**:僅台股 pe_band 且含 `derive` 區塊的標的(現況即台積電等少數檔;
   forward_eps 直填的檔次期再議)。

## 技術要點(拍板後細化)
- 資料:FinMind `TaiwanStockMonthRevenue`;月頻資料,快取以「月」為失效單位
  (store meta 或 blob 內帶抓取月,**不**沿用 EOD 邊界——月營收約每月 10 日前
  公布)。API 成本:每 derive 檔每月 +1 次 FinMind(現況約 1-6 檔)。
- 比較計算放 `valuation.compute_zones` 的 warnings(維持純函數:月營收序列經
  StockData 傳入)——**動到 valuation.py 即觸發完整黃金值 gate**:必須以測試
  證明 zones 數值逐位不變(警告純附加)。
- 顯示:沿用既有 warnings 顯示點(CLI 卡/個股分頁),零新版面。

## API 呼叫評估(草)
happy path 每月 +N 次(N=derive 檔數);設計時併入 store 快取避免日日重抓。

## PLAN(executor 填;拍板後)

## REPORT(executor 填)
