# 003 — 台股拆股還原 `_back_adjust_tw` 回歸測試(離線,全合成資料)

狀態:**CLOSED**(2026-07-06,commit 見下方收斂紀錄)
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

1. Read `_back_adjust_tw` 原始碼(L126–156),用 Bash 直接呼叫函數對七個情境跑出實際輸出,確認與手算 oracle 一致後才寫入測試(避免憑印象猜錯 factor 方向/bisect 邊界)。
2. 新增 `tests/test_back_adjust.py`,仿 `test_golden_valuation.py`/`test_roi.py` 風格:stdlib unittest、精確等值斷言(`assertEqual`,浮點皆為乾淨小數無需 `assertAlmostEqual`)、每個 class 對應一個 SPEC 情境,oracle 手算寫在測試/檔頭註解。
3. 額外補兩個邊界情境(r 剛好 =0.6 / =1.7 不觸發)與「除息日剛好落在既有價格日」的 bisect inclusive 語意,讓覆蓋更完整(SPEC 情境 4、5 的自然延伸,非額外白名單外行為)。
4. 全程只 import `_back_adjust_tw`,不碰任何 fetch/HTTP 函數;不改 `providers.py`。
5. 跑 `python -m unittest discover -s tests -v` 確認 39(既有)+ 新增全綠,並 `python -m py_compile tests/test_back_adjust.py`。

## REPORT(executor 填)

### 改動檔案
- 新增 `tests/test_back_adjust.py`(僅此一檔,白名單內)。未改動 `aimonitor/` 任何一行,未碰 `watchlist.yaml`/`config.yaml`/校正產物等禁區檔。

### 測試設計摘要
7 個 SPEC 情境對應 7 個 TestCase class(部分額外補邊界子測試):
1. `SplitBackAdjustTest` — 1:4 拆股,factor=[0.25,0.25,1.0,1.0],d1/d2 還原為 25。
2. `ReverseSplitBackAdjustTest` — 反分割 r=4.0(>1.7),factor=[4.0,4.0,1.0,1.0],d1/d2 還原為 40。
3. `ExDividendNotMisdetectedAsSplitTest` — 同情境1跳空但 d3 在 `div_history`(ex_dates)內 → 該筆跳過累乘,factor 全 1.0,價格與配息皆原樣。
4. `NormalVolatilityNoTriggerTest` — r=0.65 不觸發;另補 r 剛好 0.6 與 1.7 兩個邊界(門檻是嚴格 `<`/`>`,邊界值本身不觸發)。
5. `DividendSyncBackAdjustTest` — 斷層前配息 (d1,4.0)→×0.25=1.0;斷層後配息 (d4,1.0) 不動;另補「除息日剛好落在既有價格日 d2」驗證 `bisect_right` 對相等日期的 inclusive 語意(8.0×factor[1]=0.25→2.0)。
6. `BoundaryInputTest` — 空序列、單筆原樣;close 為 `None`/`0` 的資料點因 `if prev and cur`/`if c else c` 短路,不參與 r 計算也不被相乘、不崩潰。
7. `UnsortedInputTest` — 打亂輸入順序後,函數內 `sorted(price_history)` 排回升冪,結果等同情境1。

所有 oracle 均先用 Bash 直接呼叫 `_back_adjust_tw` 印出實際回傳值核對一致後,才寫入測試斷言與註解手算過程(未憑空猜測)。

### 實際跑過的指令與結果
```
python -m unittest discover -s tests -v
```
輸出:`Ran 52 tests in 0.002s` / `OK`(52 = 既有 39 + 新增 13)。

```
python -m py_compile tests/test_back_adjust.py
```
無輸出、exit code 0(語法通過)。

黃金值比對:本工單未改 `aimonitor/`,不涉及 `monitor.py report --ticker 2330` 數值(略,無需重跑;本次未改動估價邏輯故未跑)。

### API 呼叫評估
0 次。全合成資料;`from aimonitor.providers import _back_adjust_tw` 只 import 模組本身(模組 import 不發送任何網路請求),測試中未呼叫任何 `fetch_*`/`_finmind`/`_http_get_json` 等函數。

### 引擎疑點
無新增疑點。`_back_adjust_tw` 現狀邏輯經七情境 + 補充邊界驗證後行為一致、無矛盾:
- factor 方向(由後往前累積、`factor[i-1]=cum`)確實達成「斷層之前的所有點都被還原」的設計意圖。
- `None`/`0` close 的短路處理不會拋例外,對應 SPEC「邊界不誤判」要求。
- 除息日 inclusive 語意(`bisect_right`)在「除息日恰為某價格日」時,採用該價格日自身的 factor,語意合理(該筆配息與同日股價同尺度)。
- 未發現需要回報 orchestrator 的 bug;未修改 `providers.py`。

### 剩餘風險
- 測試只覆蓋合成資料的邊界/情境組合,未覆蓋「連續多次分割」(例如兩段不同 factor 交疊)之類更複雜序列;若未來要加可另開工單。
- 未對 `_back_adjust_tw` 之外的呼叫端(`fetch_tw` 內如何套用還原結果)做整合測試,範圍如 SPEC 所限,僅純函數層級。

## 收斂紀錄(orchestrator)

- Reviewer findings 仲裁(無 P1,oracle 全數鎖對):
  - **P2-1 採納**:原 r 樣本無法區分門檻 0.6 vs 0.5。補 r=0.5625(36/64,二進位精確,
    鎖下限數值)與 r=1.75(7/4,鎖上限數值)兩個觸發 case。
  - **P2-2 採納 (a) 誠實註解,否決 (b) 補 case**:orchestrator 推演確認
    「除息日恰逢 factor 階梯日」在此函數結構上不可能(除息日必在 ex_dates,
    同日斷層必被抑制),bisect_left/right 在所有可達輸入上等價 → 補 case 無意義,
    改為在測試註解記錄此結構不變量。
  - **P3-1 採納**:補 f_for 的 j<0 fallback 分支測試(除息日早於所有價格日 → factor[0])。
  - 另:orchestrator 修正 executor 註解中的簡體字(門檻/觸發/輸入等,repo 全繁體)。
- 收斂 gate:55/55 綠(52+3)、py_compile 綠、diff 僅白名單、orchestrator 已逐行讀 diff、
  零 aimonitor/ 變動(黃金值不受影響)。→ CLOSE。
- 流程事故記錄:orchestrator 曾誤用 PowerShell `Get-Content -Raw`/`Set-Content`
  改本檔狀態行,PS 5.1 以 ANSI 誤讀 UTF-8 導致整檔亂碼入 commit,隨即以 Write
  工具重建全文並 amend 修復(內容無遺失)。教訓:文字檔一律用 Edit/Write 工具。
