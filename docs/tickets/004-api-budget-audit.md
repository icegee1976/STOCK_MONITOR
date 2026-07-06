# 004 — API 呼叫與快取盤點(唯讀審計 → 文件)

狀態:**CLOSED**(2026-07-06,orchestrator 親自執行,唯讀)
母單:000-baseline.md 工單 004

## SPEC

### 目標
逐 code path 盤點「dashboard 冷載入 / CLI report 全清單 / report 單標的 / screen /
watch 每輪」各打 FinMind / yfinance / Finnhub 幾次,對照額度(FinMind 300/hr 免
token、600/hr 有 token;Finnhub 60/min;yfinance 無明確額度但雲端易 429),
確認 `@st.cache_data` TTL 與 `.cache/` 檔案快取 `max_age_min` 的涵蓋與失效行為,
附帶確認金鑰只存在 env / session_state(不落地)。產出 `docs/api-budget.md`。

### 允許檔案
- 新增 `docs/api-budget.md`;本工單檔。**零程式碼變更。**

### 驗收
- 文件含每指令的呼叫數上限表 + watch 最小安全 interval 建議。
- 若發現超額風險或金鑰落地,只記錄不修(另開單)。

### API 呼叫評估
0 次(純讀 code,不實跑全清單)。

## REPORT(orchestrator 填)

- 產出 `docs/api-budget.md`:每檔/每指令呼叫成本表、三層快取行為確認、
  watch 最小安全 interval 建議表。零程式碼變更、0 次 API 呼叫
  (watchlist 統計用本地 yaml 讀取)。
- 交叉驗證:台股冷載入 36 次(25 價格+6 PER+5 配息)與 commit `02a6787`
  宣稱的「75→36/載入」吻合。
- **關鍵發現 F-1(高)**:`watch --interval 300`(預設)因 `use_cache=False`
  每輪 36 次 FinMind → 432/hr,超過免費 300/hr,**跑 ~40 分鐘就 402**且拖累
  同 IP 的 dashboard。修向((a)調預設值 (b)watch 改吃 15 分快取 (c)額度預算檢查)
  屬行為變更 → 人類拍板後另開工單。
- F-2(中):Finnhub 全清單 54 次貼近 60/min,靠序列延遲當緩衝;未來若並行化會超限。
- F-3(低):watch 輪與輪之間對 429 無 backoff(有過期快取保命,不致崩)。
- 金鑰路徑確認乾淨(env/secrets/session_state,無落地)。
- 收斂 gate:唯讀審計無 diff 風險;文件由 orchestrator 自撰自查。→ CLOSE。
