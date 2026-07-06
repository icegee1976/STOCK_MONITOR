# 007 — ROI 補實作美股股利預扣 30%(引擎數值變更)

狀態:**CLOSED**(2026-07-06,commit 見下方收斂紀錄)
(人類已拍板修向:補實作,見 002 工單頭部的發現)

## 收斂紀錄(orchestrator)

- Reviewer(從嚴,引擎變更單)結論:無 P1/P2。×0.7 套用時點正確、INTL/TW 不受傷、
  `fees["us"]` 下標與既有 `_buy_cost` 同型(未引入新崩潰路徑)、render 端契約查證
  (report.py/app.py/monitor.py 均不讀新 key)、三個 oracle 獨立複核一致。
- 仲裁:
  - **P3-B 採納**:TW 對照 oracle 落在 round 半值邊界(104348.385→.38),
    改 `assertAlmostEqual(…, delta=0.011)` 去除浮點半值依賴(預扣誤套差 1497 元仍抓得到)。
  - **P3-A 記錄**:毛殖利率顯示 vs 淨額 ROI 的口徑落差 → 開 backlog 工單 009,不在本單修。
  - 另修 INTL 測試 docstring 的過時「未實作」警語(orchestrator 自查)。
- 收斂 gate:58/58 綠、py_compile 綠、黃金值 CLI 交叉確認不變(2228.57/1731.23)、
  diff 僅白名單(roi.py +8 行)。→ CLOSE。
決策理由(人類:「選更能反映現實幫助判斷的做法」):美國對非居民外國人(NRA)
股利課 30% 預扣稅是現實;不扣會系統性高估美股含息 ROI(高息標的誤差最大)。
README §5.5 與 CLAUDE.md 紅線本來就宣稱已含 → 補實作後文件變為真,無需改文件。

## SPEC(orchestrator)

### 目標
`aimonitor/roi.py::scenario_roi` 的股利累加對 **market == "US"** 乘上 (1 − 預扣稅率):
- 稅率:`config["fees"]["us"].get("dividend_withholding", 0.30)`,**預設 0.30 寫在 code**
  (`config.yaml` 是禁區,不得為此加欄位)。
- TW 不變(台股股利稅制不在本單);INTL 維持 per_share_div=0 不變。
- 結果 dict 新增 `us_div_withholding`(實際使用的稅率,US 以外為 None),供上層透明顯示
  (本單**不**改 report.py/app.py 顯示,additive 資料欄位而已)。

### 範圍註記(不做,留紀錄)
台股二代健保補充保費、愛爾蘭註冊 ETF 15% 基金層級稅 → 未來另開單。

### 允許檔案(白名單)
- `aimonitor/roi.py`(最小 diff:只動股利那一段 + 結果 dict 一個欄位)
- `tests/test_roi.py`(新增 US 股利預扣測試;既有測試不得刪)

### 測試要求(手算 oracle 入註解)
1. US:price=100、capital=10,000(shares=100)、annual_dividend=5.0、fair 目標價=100、1 年:
   divs = 100×5.0×**0.7**×1 = 350 → total=10,350 → `value_in_stock_ccy`==10350.0、
   `total_return_pct`==3.5。
2. 自訂稅率:fees.us.dividend_withholding=0.0 時 divs=500(向後相容出口)。
3. TW 對照:同構台股 case 股利**不**打折(維持現狀)。
4. 既有 55 題全綠(INTL=0 的測試不受影響)。

### 驗收(收斂 gate)
- `python -m unittest discover -s tests` 全綠;py_compile 綠。
- **黃金值**:`python monitor.py report --ticker 2330` 便宜 2228.57 / 大特 1731.23 不變
  (本單不碰 valuation;離線 12 題黃金值測試已足,CLI 實跑一次交叉確認即可)。
- diff 僅白名單;`git diff` 中 roi.py 變更行數 ≤ 15。

### 禁區
- 不動 `config.yaml`/`watchlist.yaml`;不改 report.py/app.py;不改免責聲明。

### API 呼叫評估
測試 0 次;驗收 CLI 單標的 1–2 次 FinMind(黃金值交叉確認)。

## PLAN(executor 填)

在 `scenario_roi` 計算 `per_share_div` 之後、`target_year` 之前插入:若
`market == "US"`,取 `rate = fees["us"].get("dividend_withholding", 0.30)`,
`per_share_div *= (1 - rate)`,並記錄 `us_div_withholding = rate`;非 US 維持
`us_div_withholding = None`(INTL 本已強制 per_share_div=0,不受影響)。回傳
dict 尾端新增 `us_div_withholding` 欄位(純 additive,不動既有 key)。
`tests/test_roi.py` 新增 `UsDividendWithholdingTest`(3 案例:預設 0.30、
自訂 0.0 向後相容、TW 對照不打折),既有測試與檔頭說明文字同步更新
(移除已過時的「未實作」警語),不刪除既有案例。

## REPORT(executor 填)

### DIFF 摘要
- `aimonitor/roi.py`:**+8 行**(git diff 顯示 `1 file changed, 8 insertions(+)`,
  在 ≤15 行預算內)。只動股利段:
  - 新增 `us_div_withholding` 局部變數,`market=="US"` 時取
    `fees["us"].get("dividend_withholding", 0.30)` 並對 `per_share_div` 乘上
    `(1 - rate)`。
  - 結果 dict 新增一個 key:`"us_div_withholding": us_div_withholding`。
  - 未動 `_buy_cost`/`_sell_proceeds`/`fair`/`cheap`/其他既有欄位。
- `tests/test_roi.py`:新增 `UsDividendWithholdingTest`(3 個測試方法)+ 更新
  檔頭已過時的「未實作」警語為工單 007 說明。既有測試 class/method 一個字未刪。

### 實際跑過的指令與結果
1. `python -m py_compile aimonitor/roi.py tests/test_roi.py` → 無錯誤輸出(exit 0)。
2. `python -m unittest discover -s tests -v` → **58 passed**(既有 55 + 新增 3),
   `Ran 58 tests in 0.002s` / `OK`。
   - 過程中發現手算註解一處筆誤(TW 對照組 proceeds 誤算,已修正為
     99358.385,total round 後為 104348.38),不影響引擎邏輯,純測試註解/斷言
     精度修正。
3. `python monitor.py report --ticker 2330`(黃金值交叉確認,實打 FinMind 1 次):
   - 便宜價 **NT$2,228.57**(PDF 黃金值 ≈2,226,既有 12 題離線黃金值測試已鎖此值,
     此為現價交叉確認,一致)。
   - 大特價 **NT$1,731.23**(PDF 黃金值 ≈1,729,一致)。
   - 未變動,證實本次改動未觸及 valuation.py / classify.py。

### 黃金值比對
| 項目 | PDF 黃金值 | CLI 實跑值 | 結果 |
|---|---|---|---|
| 便宜價 | ≈2,226 | 2,228.57 | 一致(未變) |
| 大特價 | ≈1,729 | 1,731.23 | 一致(未變) |

### 新增 API 呼叫評估
- 單元測試:0 次(全離線,`SimpleNamespace` stub `data`,未 import providers)。
- 驗收 CLI:`monitor.py report --ticker 2330` 打 1 次 FinMind(符合工單估計的
  1–2 次)。

### 剩餘風險
- 手算 oracle 已在測試檔案內以逐步算式註解;人工複核建議重點看
  `test_tw_dividend_not_discounted_and_withholding_field_none` 的浮點精度
  (`round(total, 2)` 後為 104348.38,非四捨五入前的 104348.385)。
- `us_div_withholding` 為新增資料欄位,本單刻意不接 `report.py`/`app.py`
  顯示(依 SPEC additive-only 範圍),上層若要顯示需另開工單。
- 台股二代健保補充保費、愛爾蘭 ETF 15% 基金層級稅仍未實作(SPEC 已註記留待
  未來另開單,非本單範圍)。
- 未觸碰 `config.yaml`/`watchlist.yaml`/`report.py`/`app.py`/`valuation.py`/
  `classify.py`/`providers.py`;`git status` 顯示另有 `monitor.py` /
  `docs/api-budget.md` 的未追蹤變更與 `docs/tickets/008-*.md` 未追蹤檔案 ——
  **這些非本次工單所改**,研判為其他並行工單(008)遺留的工作副本狀態,
  已確認未被本次操作觸碰或覆寫,列出供 orchestrator 核對。
