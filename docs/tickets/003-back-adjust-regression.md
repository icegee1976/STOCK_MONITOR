# 003 — 台股拆股還原 `_back_adjust_tw` 回歸測試(離線,全合成資料)

狀態:OPEN(等 002 close 後派工)
母單:000-baseline.md 工單 003

## SPEC(orchestrator)

### 目標
用合成價格/配息序列鎖住 `aimonitor/providers.py::_back_adjust_tw` 的還原邏輯。
此函數是純函數(不打網路),影響 price_band 百分位、波動率、殖利率河流圖——README §4 點名的關鍵啟發式。

### 允許檔案(白名單)
- 新增 `tests/test_back_adjust.py`(stdlib unittest,零新依賴)
- **只允許 `from aimonitor.providers import _back_adjust_tw`**;不得 import/呼叫任何 fetch 系列函數。

### 測試情境(每項手算 oracle 寫註解)
1. **1:4 拆股被還原**(0050 型,r=0.25 < 0.6):
   `[(d1,100),(d2,100),(d3,25),(d4,25)]`、無配息 → d1、d2 還原為 25(斷層前 ×0.25)。
2. **反分割**(r > 1.7):如 `[(d1,10),(d2,10),(d3,40),(d4,40)]` → d1、d2 還原為 40。
3. **大額除息不誤判**:同情境 1 的跳空,但 d3 出現在 `div_history` 的除息日
   → **不**還原,價格原樣返回。
4. **正常波動不觸發**(0.6 ≤ r ≤ 1.7):單日 −35%(r=0.65)不調整。
5. **配息同步還原**:情境 1 加上斷層前的配息 `(d1, 4.0)` → 還原為 1.0(×0.25);
   斷層後配息不動。
6. **邊界**:空序列 / 單筆 → 原樣返回;close 為 None/0 的資料點不觸發誤判。
7. **輸入亂序**:未排序輸入會被 `sorted()` 整成升冪後處理(鎖現狀)。

### 驗收
- `python -m unittest discover -s tests -v` 全綠(含 001、002),離線可跑。
- py_compile 綠;diff 只有白名單檔案。

### 禁區
- 不改 `providers.py`;疑點只寫 REPORT。
- 不打網路、不讀 live yaml。

### API 呼叫評估
0 次(全合成資料;import providers 模組本身不發請求)。

## PLAN(executor 填)

## REPORT(executor 填)
