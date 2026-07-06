# 010 — 過期快取保命語意寫成契約(註解 + 回歸測試,不改行為)

狀態:**CLOSED**(2026-07-06,commit 見下方收斂紀錄)
(人類授權:「你推薦做法並執行」)
決策(orchestrator):**不改行為**。stale rescue 是可用性保底,與 `use_cache`
(要不要吃新鮮快取)正交;現況無呼叫點傳 False,改行為是為不存在的功能做設計。
把「刻意如此」寫進註解 + 測試,未來做「強制刷新」的人會先撞到這兩個護欄。

## SPEC(orchestrator)

### 目標
1. `aimonitor/providers.py::fetch` 的 stale rescue 段(L377–383 附近)補 2–3 行註解:
   說明 rescue **刻意不看** `use_cache` 旗標——`use_cache` 只控制「讀新鮮快取」,
   全源失敗時的過期快取保命一律生效(可用性優先);未來若要做「強制刷新且失敗
   就報錯」的語意,須另開參數,不得偷改此處。
2. `tests/test_providers_fallback.py` 新增一個測試(併入既有 StaleCacheRescueTest):
   `use_cache=False` + 全源失敗 + 有過期快取 → 仍被救援(ok()、source 含
   「(過期快取)」)。鎖住現狀,防未來被「順手」改掉。

### 允許檔案(白名單)
- `aimonitor/providers.py`(**僅註解**,零邏輯變更;git diff 不得出現非註解行)
- `tests/test_providers_fallback.py`(新增一個測試方法)

### 驗收
- 71+1 全綠、py_compile 綠;providers.py 的 diff 逐行皆為註解;0 API。

### 禁區
- 不改任何執行邏輯;不動其他檔。

## PLAN(executor 填)

1. `providers.py` L377-378 之後補 3 行純註解:說明 stale rescue 刻意不看
   `use_cache`(該旗標只管「讀新鮮快取」,可用性保底一律生效),未來要做
   「強制刷新失敗即報錯」需另開參數,不得偷改此處。零邏輯變更。
2. `tests/test_providers_fallback.py` 在 `StaleCacheRescueTest` 內新增
   `test_use_cache_false_still_rescued_by_stale_cache_on_total_failure`:
   複製既有 stale rescue 測試但改傳 `use_cache=False`,斷言仍被救援
   (ok()==True、source 含「(過期快取)」、price 為舊值 250.0)。
3. 跑 `python -m unittest discover -s tests`、`py_compile` 兩檔、
   `git diff aimonitor/providers.py` 逐行核對皆為註解。

## REPORT(executor 填)

- providers.py 新增 3 行註解(L379-381),git diff 逐行核對皆以 `#` 開頭,零邏輯變更。
- tests/test_providers_fallback.py 在 StaleCacheRescueTest 新增 1 個測試方法
  `test_use_cache_false_still_rescued_by_stale_cache_on_total_failure`。
- `python -m py_compile aimonitor/providers.py tests/test_providers_fallback.py`:通過,0 錯誤。
- `python -m unittest discover -s tests`:73 tests, OK(0 failures, 0 errors)。
- 0 API 呼叫(全離線 mock,沿用既有 fixture 手法,未新增任何資料源呼叫)。
- 未跑黃金值比對(本工單未動估價/分類/ROI 邏輯,不適用)。
- 剩餘風險:無(僅註解 + 新增獨立測試方法,不影響既有測試與行為)。

## 收斂紀錄(orchestrator)

- **免派獨立 reviewer(orchestrator 裁決)**:providers.py diff 經逐行核對僅 3 行
  `#` 開頭註解(零邏輯);新測試為 reviewer 已審過的 StaleCacheRescueTest 模式克隆
  (僅 use_cache 旗標翻轉),docstring 明載鎖定目標與翻紅突變。
- 收斂 gate:全套測試綠(orchestrator 實跑)、py_compile 綠、diff 僅白名單、0 API。
  → CLOSE。
- 注:executor REPORT 的測試數 73 與其總結訊息的 75 不一致,係因 009 工單並行
  新增 3 題於同一時段陸續落檔;以 orchestrator 收斂時實跑為準。
