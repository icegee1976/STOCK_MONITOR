# 008 — watch 改吃檔案快取(修 004 審計的 F-1 額度炸彈)

狀態:**CLOSED**(2026-07-06,commit 見下方收斂紀錄)
(人類已拍板修向 (b):watch 改 use_cache=True)

## SPEC(orchestrator)

### 目標
`monitor.py::cmd_watch` 的 `make_fetch_fn(config, use_cache=False)` 改為
`use_cache=True`,讓 15 分鐘檔案快取吸收輪詢:
- 額度效果:免 token 從 432 FinMind/hr(40 分鐘 402)降到 ≤144/hr,預設
  `--interval 300` 變安全(見 docs/api-budget.md §5)。
- 語意正當性:資料本來就是**日收盤 EOD**(README §5.4),一天只變一次;
  15 分鐘快取對「進入便宜價」判斷零損失。
- 附帶:啟動訊息補一句快取說明(如「報價快取 15 分鐘(EOD 資料,不影響判斷)」),
  讓使用者知道為何連續兩輪數字相同。

### 允許檔案(白名單)
- `monitor.py`(cmd_watch 內 ≤ 5 行:布林 + 啟動訊息)
- `docs/api-budget.md`(F-1 標記已修 + §2/§5 表格對應列更新)

### 驗收
- py_compile 綠;既有 55 題離線測試全綠(watch 無單元測試,不強求新增——
  行為由 providers 快取層承擔,該層將由 005 補 mock 測試)。
- **不做全清單 live 冒煙**(一輪 = 36 次 FinMind,12% 額度,不值得;
  改由 orchestrator 讀 diff 確認布林傳遞路徑 make_fetch_fn → providers.fetch)。
- diff 僅白名單。

### 禁區
- 不動 providers.py、config.yaml、alert 預設值;不改 watch 的提醒邏輯/去重邏輯。

### API 呼叫評估
0 次(不做 live 冒煙)。

## PLAN(executor 填)

1. `monitor.py::cmd_watch`:`make_fetch_fn(config, use_cache=False)` → `use_cache=True`,
   讓輪詢改讀 15 分鐘檔案快取(cache miss 才真的打 FinMind/Finnhub/yfinance)。
2. 在既有啟動訊息後加一行 `report._p(...)` 說明快取行為,避免使用者誤以為連續兩輪
   數字相同是 bug。
3. 同步更新 `docs/api-budget.md`:§2 watch 列、§4 F-1、§5 表格,標記已修並更新額度數字。
4. 跑 `py_compile` + `unittest discover`(離線),不做 live 冒煙(SPEC 明定,省 36 次 FinMind)。

## REPORT(executor 填)

### DIFF 摘要
- `monitor.py`(cmd_watch 內,2 行變更,遠低於 ≤5 行上限):
  - L160:`make_fetch_fn(config, use_cache=False)` → `use_cache=True`
  - L169(新增 1 行):啟動訊息後補一句快取說明
    `"[dim]報價快取 15 分鐘(EOD 資料,不影響判斷),同一快取週期內多輪共用資料以節省 API 額度。[/dim]"`
- `docs/api-budget.md`:
  - §2 表格「watch --interval 300」列:432/hr → ≤144/hr,標記「已修工單 008」
  - §4 F-1:標題加「—— 已修(工單 008)」,內文改為敘述已採用修向 (b) 及效果
  - §5 表格:兩條舊行為列標記「(已修)」+ 刪除線建議值,新增一列「use_cache=True(已修,工單 008,現行行為)」確認 300s 預設值安全

### 實際跑過的指令與結果
- `python -m py_compile monitor.py` → 無輸出(通過)
- `python -m unittest discover -s tests` → `Ran 55 tests in 0.001s` / `OK`(全綠,離線,0 API 呼叫)
- 未做全清單 live 冒煙(依 SPEC,省 36 次 FinMind)

### 黃金值比對
不適用——本工單未動估價/分類/ROI 引擎(providers.py、classify.py、valuation.py 均未觸碰),
不影響 report --ticker 2330 的數值路徑。

### 新增 API 呼叫評估
0 次。本次變更僅改變 watch 輪詢的快取讀取策略(cache hit 時完全不打 API),
未執行任何 live 冒煙或新增資料源呼叫。

### 剩餘風險
- watch 無單元測試覆蓋(SPEC 已註明此為既有現況,行為由 providers 快取層承擔，
  該層測試由工單 005 補強),本工單未新增測試。
- 快取 TTL 固定 15 分鐘來自 `config.providers.cache_minutes`,未在本工單調整,
  若使用者未來改小 TTL 可能重新逼近舊有額度風險(不在本工單範圍內)。

## 收斂紀錄(orchestrator)

- **免派獨立 reviewer(orchestrator 裁決)**:diff 僅 2 行程式 + 純文件更新;
  布林傳遞路徑(make_fetch_fn → providers.fetch → _load_cache(cache_minutes=15))
  由 orchestrator 對照 providers.py L346–384 端到端查證;語意正當性
  (EOD 資料一天一變,15 分快取零損失)已在 SPEC 論證。
- 收斂 gate:py_compile 綠、55 題離線測試綠(executor 實跑)、diff 僅白名單、
  0 API 呼叫。→ CLOSE。
- 效果:watch 預設 interval 300 從「40 分鐘 402」變安全(≤144 FinMind/hr)。
