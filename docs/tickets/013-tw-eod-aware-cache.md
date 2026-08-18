# 013 — 台股 EOD-aware 快取失效(候選 A;人類已核准 A→B→C→D)

狀態:**CLOSED**(2026-08-18,commit 見文末收斂紀錄)

## 收斂紀錄(orchestrator)

- Reviewer(從嚴,快取層必審)結論:無 P1——邊界數學七案例逐格手演+實跑全對、
  stale-rescue 逐行未變、4 天 stamp 不 flaky(96h 窗必含邊界,最大無邊界間隔 72h)。
  P2×5 / P3×6 仲裁如下,修正包由原 executor 二輪完成:
  - **P2-1 採納**:`tw_eod_hour` 防呆(`_tw_eod_hour()`:try/except + 0–23 夾限回退 18),
    快取讀取路徑恢復「永不往外炸」性質;+8 測試含非法值 None/"18:00"/25。
  - **P2-3/P3-5 採納(本輪最重要)**:reviewer mutation 實測證明原 4 條整合測試對
    「接線退回固定 TTL」零鑑別。加 `_now_tpe()` 可注入時鐘,補「TW 快取 3 小時前、
    未跨邊界 → 0 呼叫」鑑別測試;executor 重演 mutation:突變下 FAIL 1、還原後
    99 全綠、對照組(舊 4 條)突變下仍全綠——鑑別力有雙方獨立證據。US 20 分測試
    釘假時鐘去除 18:00–18:20 弱時段。
  - **P2-2 採納**:naive 戳記註解誠實化(台灣本機=精確;UTC 雲端=保守多抓一輪,
    容器不保留 .cache 故可忽略;UTC+8 以東=反向偏差、一次性有界——不再宣稱全面保守)。
  - **P2-4 記錄+延後**:cache key 不含 history_years/估價 method,013 把既有不一致窗
    從 15 分放大到最長 ~72h;正解(參數入儲存層)歸入 B(本地歷史庫)SPEC,
    本單於 api-budget §3 補清快取提示。
  - **P2-5/P3-2/P3-3 採納**:api-budget 恢復最壞情況框架(失敗不寫快取,402 期間
    watch 仍 432/hr)、刪無出處的 96%、§3.2 改為 rmtree 實況、§3.3 use_cache 修正。
  - **P3-1 採納**:watch 啟動訊息更新(白名單由 orchestrator 明示擴充 monitor.py 一行)。
  - **P3-4 採納**:test_eod_cache 補 os.environ 快照。
  - **P3-6 更正**:逐日迴圈任何輸入最多 ~4 次即返回(遇首個邊界即 False,最長無邊界
    區間 3 天),REPORT 原「跨度大會迴圈較多」描述方向錯誤,以此為準。
- 收斂 gate:99/99 綠(90+9)、py_compile 綠、黃金值 2228.57/1731.23 不變
  (二輪驗證 0 額外 FinMind,吃新鮮快取)、diff 僅白名單、orchestrator 逐行讀
  兩輪 diff。效果:台股 FinMind 由理論 144/hr 降至 happy path ~36/天
  (失敗模式上界仍 36/輪,已如實寫進 api-budget)。→ CLOSE。

## 背景與目標
台股資料是日收盤(EOD),FinMind 收盤後約傍晚才更新當日資料(README §5.4)——
一天只變一次。但檔案快取 TTL 固定 15 分鐘,活躍使用時整天重抓不會變的資料
(watch/dashboard 理論上限 ~144 FinMind/hr)。改成「資料更新邊界感知」:
**台股快取在「上次抓取之後沒有跨過任何資料更新邊界」時視為新鮮**,
FinMind 用量降到 ~36/天級距。**美股/INTL 完全不動**(盤中報價會動,改日更
屬語意變更,另案拍板——工單內註記即可)。

## SPEC(orchestrator;設計決策已定,executor 不得偏離)

### 新鮮度規則
- 新增純函數 `_tw_cache_fresh(fetched_at: datetime, now: datetime | None = None,
  eod_hour: int = 18, floor_minutes: float = 15.0) -> bool`(providers.py):
  1. **時區**:台北 = 固定 `timezone(timedelta(hours=8))`(台灣自 1979 無 DST,
     **不得**引入 zoneinfo/tzdata 依賴——Windows 無系統 tz db)。兩個時間都轉成
     UTC+8 aware 再比。`now=None` → `datetime.now(tz=UTC+8)`。
  2. **邊界**:每個週一~週五的台北 `eod_hour`:00(預設 18:00,對應 FinMind
     傍晚更新,留餘裕)。規則:`(fetched_at, now]` 區間內**不含任何邊界** → 新鮮。
     週末無邊界(週五晚抓的整個週末新鮮,直到週一 18:00)。國定假日不查行事曆,
     視同交易日——每逢假日多抓一次,可接受,免除行事曆依賴。
  3. **安全地板**:`now - fetched_at < floor_minutes` 一律新鮮(保底,防時區/
     時鐘錯誤造成重抓風暴;沿用 config `cache_minutes`)。
  4. **Legacy naive 時間戳**:既有快取 `_fetched_at` 是 naive isoformat。
     naive → **一律當作台北時間**解讀:本機正確;雲端(UTC 時鐘)會把抓取時間
     看老 8 小時 → 偏向多抓一次,保守方向,可接受。**新寫入改為 tz-aware UTC
     isoformat**(`datetime.now(timezone.utc).isoformat()`),讀取端相容兩種。
     ⚠ aware vs naive 直接比較會 TypeError——先正規化再比,測試必須涵蓋。

### fetch() 接線
- `fetch()` 的快取讀取:`market == "TW"` 時改用 `_tw_cache_fresh` 判斷
  (取代固定 TTL);US/INTL 維持 `_load_cache(market, ticker, cache_min)` 原樣。
- `_load_cache`/`_save_cache` 改動最小化:建議 `_load_cache` 增加回傳 fetched_at
  的途徑或新增 TW 專用讀取路徑,executor 設計,但 **stale-rescue(max_age=None)
  與過期快取標記行為一字不改**(有 010 的契約測試盯著)。
- `providers_cfg` 可選覆寫 `tw_eod_hour`(預設 18 寫在 code)。**不動 config.yaml**(禁區)。

### 測試(離線,0 API;新檔 tests/test_eod_cache.py 或併入 test_providers_fallback.py)
1. 純函數表格測試(顯式傳 fetched_at/now,全 aware UTC+8):
   盤中同日(10:00→14:00)新鮮;跨當日邊界(10:00→19:00)過期;
   邊界後→隔日邊界前(19:00→隔日 10:00)新鮮;週五 19:00→週日新鮮;
   週五 19:00→週一 17:59 新鮮;→週一 18:01 過期;floor(now-5min 跨邊界仍新鮮);
   naive legacy 台北解讀;aware UTC 輸入正規化;naive/aware 混用不炸。
2. 整合(沿用 005 的 mock 基建):TW 快取 stamp = now−10min → fetch 0 呼叫;
   stamp = now−4天(任意 now 必含 ≥1 邊界,72h 是最大無邊界間隔)→ fetch 重抓;
   US 快取行為與改前一致(15 分 TTL,回歸不變)。
3. 既有 76 題全綠(尤其 005/010 的快取與 stale-rescue 契約)。

### 文件
- `docs/api-budget.md`:§2 watch 列、§3 快取層、§5 表格更新為 EOD 後數字
  (TW ~36/天;US 不變),標記工單 013。

### 允許檔案
`aimonitor/providers.py`、`tests/`(新測試檔或擴充既有)、`docs/api-budget.md`、本工單。

### 禁區
- config.yaml/watchlist.yaml;stale-rescue 行為;US/INTL 快取語意;估價/分類/ROI 一概不碰。

### API 呼叫評估
0 新增(純減量);驗收含一次 CLI 單標的黃金值交叉(1–2 次 FinMind)。

## PLAN(executor 填)

1. `providers.py` 新增 `TPE = timezone(timedelta(hours=8))` 常數、`_parse_fetched_at`(naive→
   當台北時間貼標籤;aware→astimezone 轉台北)、`_cache_age_minutes`(兩者正規化後才相減)、
   純函數 `_tw_cache_fresh(fetched_at, now, eod_hour, floor_minutes)`。
2. `_load_cache` 介面最小化改動:拆出 `_load_cache_raw(market, ticker) -> (StockData, fetched_at) | None`
   只負責「讀檔 + 解析,不做新鮮度判斷」;`_load_cache(market, ticker, max_age_min)` 改成呼叫
   `_load_cache_raw` 再套用固定 TTL(US/INTL 用,行為不變,只是底層改用 aware 台北時間相減避免
   naive-vs-aware TypeError)。`fetch()` 的 TW 分支改呼叫 `_load_cache_raw` 拿到 `fetched_at`,
   自己套 `_tw_cache_fresh`;US/INTL 分支維持呼叫 `_load_cache(market, ticker, cache_min)` 原樣。
3. `_save_cache` 的 `_fetched_at` 改寫成 `datetime.now(timezone.utc).isoformat()`(aware UTC);
   `_load_cache_raw`/`_tw_cache_fresh` 都能相容讀到 legacy naive 與新 aware 兩種格式。
4. `tw_eod_hour` 從 `providers_cfg.get("tw_eod_hour", 18)` 讀,預設寫死在 code,不動 config.yaml。
5. `stale rescue`(`_load_cache(market, ticker, None)`)完全不動——TW/US 共用同一份
   `_load_cache`,`max_age_min=None` 時直接跳過 TTL 檢查回傳,語意與改動前一致。

## REPORT(executor 填)

### DIFF 摘要
`aimonitor/providers.py`:
- import 新增 `timezone`。
- 新增 `TPE`、`_parse_fetched_at`、`_cache_age_minutes`、`_tw_cache_fresh`、`_load_cache_raw`。
- `_load_cache` 重構為呼叫 `_load_cache_raw` + 固定 TTL 比較(US/INTL 行為不變)。
- `_save_cache` 的 `_fetched_at` 改成 `datetime.now(timezone.utc).isoformat()`。
- `fetch()` 開頭的快取讀取分流:`market == "TW"` 走 `_load_cache_raw` + `_tw_cache_fresh`
  (`eod_hour` 可由 `providers_cfg.tw_eod_hour` 覆寫,預設 18;`floor_minutes` 沿用
  `cache_min = providers_cfg.get("cache_minutes", 15)`);其餘市場維持
  `_load_cache(market, ticker, cache_min)` 原樣不動。stale-rescue 段落(`_load_cache(..., None)`)
  完全未觸碰。

新增測試 `tests/test_eod_cache.py`(14 題):
- `TwCacheFreshPureFunctionTest`(10 題):同日邊界前/後、跨日邊界前後、週五晚→週日、
  週五晚→週一邊界前/後、地板凌駕邊界(含地板不足時仍過期的對照組)、legacy naive 當台北時間、
  aware UTC 正規化、naive/aware 混用不炸(含 `now=None` 走 `datetime.now(tz=TPE)`)。
- `TwEodCacheFetchIntegrationTest`(4 題,mock 紀律同工單 005):TW 快取 10 分鐘前(地板內)
  → 0 網路呼叫;TW 快取 4 天前(必含 ≥1 邊界)→ 重新打 FinMind 1 次、拿到新資料;US 快取
  10 分鐘前(15 分固定 TTL 內)→ 0 網路呼叫(回歸鎖);US 快取 20 分鐘前(超過 15 分固定 TTL)
  → 重新走 yfinance、拿到新資料(回歸鎖,證明 TW 的 EOD 規則沒有誤套用到 US)。

`docs/api-budget.md`:§2(watch/Dashboard 冷載入兩列標註工單 013、TW 額度改成 ≈36/天)、
§3(第 1 點新增 TW EOD-aware 說明,US/INTL 明示不變)、§5(表格新增 TW EOD-aware 列,
標記工單 013;保留舊列並劃線標示已不適用,方便對照演進)。

### 測試
- `python -m py_compile aimonitor/providers.py tests/test_eod_cache.py` → 無錯誤。
- `python -m unittest discover -s tests` → **90 題全綠**(既有 76 + 新增 14),0 API。

### 黃金值交叉
`python monitor.py report --ticker 2330`:便宜價 **NT$2,228.57**、大特價 **NT$1,731.23**
(對照 PDF ≈2,226/≈1,729,精確鎖定值不變)。第二次緊接著再跑一次同指令,價格與估值輸出
完全一致(讀到剛寫入的新鮮快取,`.cache/TW_2330.json` 的 `_fetched_at` 已確認寫成
`2026-08-18T04:47:27.009275+00:00` 這種 aware UTC isoformat 格式)。FinMind 呼叫:1 次
線上(首次 cache miss,`report --ticker 2330` 走 price_band 或既有 watchlist 設定的 method,
未見額外 PER/配息呼叫超出既有 §1 成本表)。

### 剩餘風險
- ~~`docs/api-budget.md` §3 第 3 點...use_cache=False...~~ **已在 reviewer 修正包第 5(d) 點修正**
  (見下方小節),不再是殘留風險。
- `_tw_cache_fresh` 的邊界掃描用「逐日迴圈」實作(fetched_at.date() 到 now.date() 的 inclusive
  range),對於快取戳記與現在時間跨度極大(例如工單 013 SPEC 沒要求但理論上可能發生的多年
  未更新)理論上會迴圈較多次,但實務上快取檔案很少超過幾天不更新(有 stale-rescue 機制介入),
  效能影響可忽略。
- 未對 `app.py`(dashboard 層 `@st.cache_data`)做任何改動——`analyze_stock` 的 ttl=900 秒
  快取仍然存在,TW 的 EOD-aware 效果只在其之下的 providers 檔案快取層生效;dashboard 端仍會
  在 15 分鐘後 rerun `analyze_stock`,但因為 providers 層檔案快取已經新鮮,不會真的打 FinMind
  ——與 SPEC 描述的「providers 層降量,dashboard 層不動」一致,未逾工單範圍。

---

## REVIEWER 修正包(第二輪,executor 執行,無 P1;白名單擴充 monitor.py 一行)

### 逐條處理

1. **P2-1 防呆**:新增 `_tw_eod_hour(providers_cfg) -> int`(providers.py):
   `try: int(...) except (TypeError, ValueError): return 18`,再夾限
   `0 <= h <= 23` 否則回退 18。`fetch()` TW 分支的
   `int(providers_cfg.get("tw_eod_hour", 18))` 改呼叫這個函數。新增
   `TwEodHourSanitizationPureFunctionTest`(7 題:缺 key、None、`"18:00"`、25、
   -1、合法邊界 0/23、合法數字字串 `"20"` 會被尊重)+ 1 題整合測試
   `test_illegal_tw_eod_hour_values_do_not_crash_and_behave_like_default_18`
   (`subTest` 跑 None/`"18:00"`/25 三種非法值,`_now_tpe` 釘死時鐘,驗證都不炸、
   都與預設 18 同結果:新鮮、0 網路呼叫)。
2. **P2-3 接線鑑別 + P3-5 mutation 重演**:新增 `_now_tpe() -> datetime`
   (`return datetime.now(tz=TPE)`),`_tw_cache_fresh`/`_cache_age_minutes` 的
   `now=None` 預設改呼叫它(行為不變,只是可測)。新增整合測試
   `test_tw_cache_3h_old_same_day_no_boundary_crossed_zero_network`:
   `_now_tpe` 釘死週三 13:00(台北),TW 快取 stamp=同日 10:00(3 小時前、未跨
   18:00 邊界)→ 斷言 0 網路呼叫。也把既有 `test_us_cache_20min_old_...` 測試改
   用釘死的假時鐘(週三 13:00,刻意避開 18:00–18:20)取代真實 wall-clock。
   **Mutation 重演**(見下方「測試」小節的實際輸出):把 `providers._tw_cache_fresh`
   monkeypatch 成「退化回固定 TTL、忽略邊界掃描」的版本,新測試
   `test_tw_cache_3h_old_same_day_no_boundary_crossed_zero_network` 從 pass 翻紅
   (`AssertionError: 1 != 0`);另外驗證 reviewer 的原始論斷——4 個舊整合測試
   (10min/4days/US-10min/US-20min)在同一個 mutation 下**全部照樣通過**
   (0 failures),證實它們對這個突變確實零鑑別力,新測試補上了這個缺口。
   還原 monkeypatch 後,`python -m unittest discover -s tests` 完整跑一次
   99 題全綠。
3. **P2-2 註解誠實化**:重寫 `_parse_fetched_at` 的 docstring,拿掉「只會偏向
   多抓一次,方向保守」這句與事實不符的講法,改成三段分開講:本機台灣執行
   =精確;雲端系統時鐘=UTC(常見)=保守多抓、且雲端容器通常不持久化
   `.cache/`,影響最多是重新部署後那一輪重抓、一次性；系統時鐘在 UTC+8
   **以東**(如 UTC+9~+14)=方向反過來,age 可能算出負值、過期資料被誤判
   新鮮,直到該筆快取被下一次成功抓取覆寫為止(一樣一次性有界,但不再保守,
   不宣稱恆安全)。
4. **P3-1**:`monitor.py` `cmd_watch` 啟動訊息那一行,改成
   「報價快取:台股至下一個收盤更新邊界(平日約 18:00)、美股/全球 15 分鐘
   (EOD 資料,不影響判斷)。」——只動這一行,其餘 `monitor.py` 不變。
5. **P2-5/P3-2/P3-3/P2-4 文件(`docs/api-budget.md`)**:
   (a) §2 的 Dashboard 冷載入 / `watch` 兩列恢復「happy path vs worst case」框架:
   happy path ≈36/天(TW),但抓取失敗時 `_save_cache` 不寫入,下一輪仍是全
   cache miss,`--interval 300` 在持續失敗期間仍是 432/hr,補了這句失敗模式
   註記,不再讓人誤以為 EOD-aware 對失敗情境也有保護。
   (b) 拿掉推導不出來的「降約 96%」數字,改成「happy path 下遠低於風險線;
   確切降幅視實際使用時數/輪詢頻率而定,未量化」。
   (c) §3 第 2 點改寫成符合 `app.py:314–319` 實況:側邊欄「🔄 重新抓取(清快取)」
   不只是 `st.cache_data.clear()`,還會 `shutil.rmtree(.cache/)`,按下去就是
   全清單冷載入(36 FinMind + 54 Finnhub + ~64 Yahoo),不是先前文件講的
   「仍會命中檔案快取」。
   (d) §3 第 3 點的 `use_cache=False` 舊敘述改成 `use_cache=True`(對照
   `monitor.py:160`,工單 008 已修)。
   (e) §3 新增第 4 點:快取鍵(`market_ticker`)不含 `history_years`/
   `valuation.method`,只改 `watchlist.yaml` 估價假設不換 ticker 時,舊快取仍會
   被當新鮮沿用,US/INTL 最長 15 分、TW EOD-aware 最長約 72 小時才反映;要立即
   生效請按側邊欄清快取或刪 `.cache/`;真正解法(快取鍵含假設指紋/本地歷史庫)
   留待未來工單。
6. **P3-4**:`tests/test_eod_cache.py` 的 `TwEodCacheFetchIntegrationTest` 加上
   `setUp`/`tearDown` 的 `patch.dict(os.environ)` 快照還原(比照
   `tests/test_providers_fallback.py` 的 `ProvidersFallbackTestCase`),原本裸
   `os.environ.pop("FINNHUB_API_KEY", None)` 現在測試結束會自動還原環境變數。

### 測試(reviewer 修正包)
- `python -m py_compile aimonitor/providers.py monitor.py tests/test_eod_cache.py` → 無錯誤。
- `python -m unittest discover -s tests` → **99 題全綠**(第一輪 90 題 + 本輪新增 9 題:
  `TwEodHourSanitizationPureFunctionTest` 7 題 + `test_illegal_tw_eod_hour_values_...`
  1 題 + `test_tw_cache_3h_old_same_day_no_boundary_crossed_zero_network` 1 題),0 API。
- **Mutation 重演**(監控輸出,原始 stdout 摘錄):
  ```
  === STEP 1: apply mutation, run the discriminating test ===
  test_tw_cache_3h_old_same_day_no_boundary_crossed_zero_network ... FAIL
  AssertionError: 1 != 0
  mutation run -> failures: 1 errors: 0 (expect >=1 failure)

  === STEP 2: revert mutation, re-run full suite ===
  Ran 99 tests in 0.158s
  OK
  full suite after revert -> ran: 99 failures: 0 errors: 0
  MUTATION REPLAY OK: caught under mutation, green after revert.
  ```
  另外對照組(獨立跑一次):同一個 mutation 下,4 個第一輪既有整合測試
  (10min/4days/US-10min-regression/US-20min)`ran: 4 failures: 0 errors: 0`
  ——證實 reviewer 的原始論斷(這 4 條測試對此 mutation 零鑑別力),新測試
  補上這個缺口。

### 黃金值交叉(reviewer 修正包後重跑)
`python monitor.py report --ticker 2330`:便宜價 **NT$2,228.57**、大特價
**NT$1,731.23**(對照 PDF ≈2,226/≈1,729,不變)。命中 reviewer 修正包前已寫入
的新鮮快取,0 額外 FinMind 呼叫。

### 新增 API 呼叫評估
0。全部改動都在快取判斷/防呆/測試/文件層,沒有新增任何線上呼叫路徑。

### 剩餘風險(reviewer 修正包後)
- 無新增風險;上一輪 REPORT 的第一條殘留風險(§3 第 3 點文件與程式碼不一致)
  已在本輪修正,其餘兩條(逐日迴圈邊界掃描效能、app.py `@st.cache_data` 未動)
  維持原判斷不變。
