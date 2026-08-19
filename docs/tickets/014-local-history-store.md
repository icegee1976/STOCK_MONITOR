# 014 — 本地歷史庫 + 台股增量抓取(候選 B 核心;Max)

狀態:**CLOSED**(2026-08-19,commit 見文末收斂紀錄)
後續:015(缺日偵測 + 品質警告)依賴本單;016(歷史庫硬化)承接延後項。

## 收斂紀錄(orchestrator)

- 兩輪交付。Reviewer(從嚴)抓到**兩條真 P1**(皆以真實情境重現,非推測),
  修正包由原 executor 完成:
  - **P1-1(可讀不可寫)採納根修**:`_sync_and_assemble` 改為無條件記憶體 merge
    (raw 覆蓋 store 回讀),寫失敗自癒;timeout 5→2s。收斂驗證:orchestrator
    親跑 reviewer 的真鎖 probe → ok=True、新舊列俱在(修復前:空資料+空錯誤+
    blob 不更新+額度保護失效)。
  - **P1-2a 採納**:price 增量回空 → 誠實走 error→stale-rescue「(過期快取)」
    (增量起點含當天必有重疊,回空必屬異常,誤傷面零)。
  - **P1-2b 採納**:PER 尾端與 price_date 差 >10 天不派生 per/trailing_eps/
    dividend_yield(ROI 紅線值恢復「同一回應」的天然偏差上界;per_history 不受限)。
  - **P2-1/P2-2 採納(dividend 撤增量)**:FinMind start_date 過濾公告日而非除息日
    (游標語意不匹配)+ PK 使同日多列 SUM→last-wins;配息增量零收益純風險 →
    恢復 014 前原始碼路徑(全量+raw 組裝,SUM 保留),store 僅 best-effort 備份。
  - **P2-3/P2-4/P2-6 記錄**:歷史值凍結為增量固有代價、use_cache=False 不涵蓋
    store(非新鮮度快取)、鎖競爭最壞 ~8s 阻塞(timeout 2s 後)——皆入 api-budget
    §3.5;定期全量重同步/schema migration/修剪等歸 016 backlog。
  - **P2-5/F5/F6**:文件過度承諾修正(寫失敗安全現為真且有測試);user_version=1
    蓋章;三處測試假陰性補斷言;方法完整回切測試補齊。
  - Mutation 證據:A(永遠全量)/B′(還原時機,F1 後的有效配方)/C(merge 拿掉)
    三種皆翻紅、還原後 138 全綠;B 的鑑別路徑位移被誠實記錄。
- 收斂 gate:138/138 綠(99 既有原封 + 39 新)、py_compile 綠、黃金值 2228.57/
  1731.23 經真實 EOD 邊界重抓路徑交叉不變、reviewer 原始 P1 probe 全數轉綠、
  S8 呼叫數不變、0 新增 API。→ CLOSE。

## 目標
在 providers 之下加一層 **stdlib sqlite3 耐久歷史庫**,讓台股在 blob 快取過期後只抓
「上次之後的增量」而非整段 5 年;同時根治 013 記錄的 P2-4(cache 不含
`history_years`/method 維度)於台股路徑。**疊加不取代**:blob 快取、EOD 新鮮度、
stale-rescue 全部原樣。

## SPEC(orchestrator;架構決策已定,不得偏離)

### D1. 儲存
- 新模組 `aimonitor/history_store.py`,單檔 `CACHE_DIR/history.sqlite3`(跟隨
  `providers.CACHE_DIR`,測試靠 patch CACHE_DIR 自然隔離)。stdlib sqlite3、
  WAL、每次操作獨立連線(with 包裹,Streamlit 多執行緒安全)。
- 表:
  - `daily_price(market,ticker,date,close, PRIMARY KEY(market,ticker,date))`
  - `daily_per(market,ticker,date,per,dividend_yield, PK 同上)`
  - `dividend(market,ticker,ex_date,cash, PK(market,ticker,ex_date))`
  - `series_meta(market,ticker,dataset,requested_start,last_success, PK(market,ticker,dataset))`
- **全部存 FinMind 原始值(未還原)**;還原在讀端組裝後執行(見 D3)。
- **永不炸原則**:store 所有公開函數內部 try/except,任何 sqlite 例外(損毀/鎖/
  唯讀)→ 回傳「無資料/不可用」讓呼叫端走全量抓取,絕不向外拋。

### D2. fetch_tw 接線(僅台股增量)
- 對每個需要的 dataset(Price 恆需;PER 限 pe_band;Dividend 限 yield_band):
  1. 讀 `series_meta.requested_start`。若**本次要求窗起點 < 記錄值**(history_years
     加深)或無記錄(首次/新 dataset,含 method 切換)→ **全量抓**(start=要求窗
     起點),成功後 upsert + 更新 requested_start/last_success。
  2. 否則**增量抓**:start = store 內該序列 MAX(date)(含當天,重疊 1 筆靠 PK
     upsert 吸收,防尾筆修訂)。
  3. 抓取失敗:沿用現行錯誤語意(402 訊息等**逐字不變**);store 已有資料時
     不得因增量失敗而讓整檔變 error——組裝 store 現有窗續用,但 price/price_date
     的「現價」新鮮度仍由上層 blob 快取/stale-rescue 決定,**不要**在本層另造
     一套新鮮度(避免與 013 疊床架屋)。實作準則:增量失敗 → 整個 fetch_tw 回傳
     error(現狀行為),stale-rescue 照舊接手;store 的價值在下次成功時仍只需增量。
  4. 組裝:從 store 取 `date >= 要求窗起點` 全序列 → **此時才** `_back_adjust_tw`
     → 之後邏輯(trailing_eps 取最後 PER 列、annual_dividend 累加等)與現狀相同,
     以組裝後序列的最後一筆為準。
- **呼叫數不變**:每 dataset 仍 1 次 `_finmind`;S8 契約測試(price_band=1/
  pe_band=2/yield_band=2)必須原封通過。

### D3. 美股/INTL:全量替換快照
- `fetch_us`/`fetch_us_finnhub` 成功取得 price_history 後,`daily_price` 整檔
  DELETE+INSERT 快照(耐久用,讀路徑不變)。**禁止增量**:yfinance auto_adjust=True
  會回溯改寫全序列(拆股/配息調整),增量拼接必毀損;INTL total-return 語意
  (roi 股利=0 前提)亦依賴之——在 code 註解寫明此禁令與原因。

### D4. 關鍵測試(離線 0 API;新檔 tests/test_history_store.py;鑑別力照 013 教訓設計)
1. store CRUD/upsert 冪等;sqlite 檔損毀(寫垃圾 bytes)→ 讀寫皆回「不可用」不炸,
   fetch_tw 退回全量抓取仍成功。
2. **增量行為鑑別**(用 005 的 URL router 捕捉 `start_date` 參數):首抓 start=窗起點;
   第二次抓 start=store MAX(date)——若接線退化成永遠全量,此斷言翻紅。
3. **拆股跨增量**(本單命脈):首次增量存入拆股前原始價,第二次增量存入拆股後
   (r<0.6);組裝+還原後序列 == 一次性全量抓取再還原的結果(逐值相等)。
   除息不誤判案例同樣跨增量驗一次。
4. **P2-4 根治**:history_years 5→10 → 觸發全量回填(URL start 變早、requested_start
   更新);method price_band→pe_band → 新增 PER dataset 抓取;切回後不重抓(meta 記憶)。
5. 窗切片:store 有 10 年、要求 5 年 → 回傳序列起點正確。
6. US 快照:兩次成功抓取,store 為第二次全量(無混拼);讀路徑輸出與改前逐位相同。
7. **既有 99 題原封全綠**(005/010/013 契約,S8 呼叫數,黃金值 12 題)。
8. 每題註解「鎖什麼、什麼突變翻紅」;executor 交付前自行 mutation 重演至少
   「退化成永遠全量」與「還原時機移到存入前」兩種,結果寫 REPORT。

### 允許檔案
新增 `aimonitor/history_store.py`、`tests/test_history_store.py`;修改
`aimonitor/providers.py`(fetch_tw 接線 + US 快照掛點,blob/新鮮度/rescue 區塊零改動);
`docs/api-budget.md`(§3 補歷史庫層說明、§3.4 P2-4 台股已根治註記);本工單。

### 禁區
- blob 快取讀寫/EOD 判斷/stale-rescue 的既有行為與字串;valuation/classify/roi;
  config.yaml/watchlist.yaml;`_back_adjust_tw` 本體;US 讀取語意。

### API 呼叫評估
0 新增(次數不變、payload 變小);黃金值交叉驗收 1–2 次 FinMind。

## PLAN(executor 填)

1. 新增 `aimonitor/history_store.py`:純儲存層,**不 import providers**(避免循環 import)——
   所有公開函數第一個參數都是顯式 `cache_dir: str`,由呼叫端(providers.py)傳入自己
   模組全域的 `CACHE_DIR`(late-binding,測試 `patch.object(providers, "CACHE_DIR", tmp)`
   時 providers.py 內對 `CACHE_DIR` 的參照自然跟著變,滿足 SPEC「跟隨 providers.CACHE_DIR」)。
   每次操作 `with sqlite3.connect(...)` 開關獨立連線 + `PRAGMA journal_mode=WAL`。
   公開介面(全部「永不炸」:內部 try/except,例外→讀回 `None`/寫回 `False`,絕不外拋):
   ```
   upsert_price(cache_dir, market, ticker, rows:[(date,close)]) -> bool
   upsert_per(cache_dir, market, ticker, rows:[(date,per,dividend_yield)]) -> bool
   upsert_dividend(cache_dir, market, ticker, rows:[(ex_date,cash)]) -> bool
   get_price(cache_dir, market, ticker, start_date=None) -> list|None   # None=不可用,[]=真的無資料
   get_per(cache_dir, market, ticker, start_date=None) -> list|None
   get_dividend(cache_dir, market, ticker, start_date=None) -> list|None
   max_date(cache_dir, market, ticker, dataset:"price"|"per"|"dividend") -> str|None
   get_meta(cache_dir, market, ticker, dataset) -> {"requested_start","last_success"}|None
   set_meta(cache_dir, market, ticker, dataset, requested_start, last_success) -> bool
   replace_us_snapshot(cache_dir, market, ticker, rows) -> bool   # DELETE+INSERT 同一交易
   ```
2. `providers.py` 新增私有 helper `_sync_and_assemble(dataset, cache_dir, ticker,
   requested_start, fetch_fn, upsert_fn, get_fn)`:讀 meta 決定全量/增量 → 呼叫
   `fetch_fn(start)`(內部打 `_finmind`,例外原樣往外拋,不吞)→ upsert(best-effort)→
   set_meta(best-effort)→ 從 store 取 `date>=requested_start` 組裝序列;store 不可用
   (`None`)時退回這次剛抓到、尚未入庫的原始列,保證這次呼叫仍成功。
3. `fetch_tw` 改用 `_sync_and_assemble` 分別跑 price(恆抓)/per(`pe_band`)/dividend
   (`yield_band`)三段;price 例外仍在最外層 try 捕捉、402 訊息逐字保留、直接 `return d`
   (不嘗試從 store 救援——SPEC 明文「增量失敗→整個 fetch_tw 回傳 error(現狀行為)」);
   per/dividend 仍是 `except Exception: pass`(非致命,現狀不變)。**`_back_adjust_tw`
   移到組裝完 price_history/div_history 之後才呼叫**(D2 point 4 命脈)。
4. `fetch_us`/`fetch_us_finnhub`:price_history 成功組出來的當下,呼叫
   `history_store.replace_us_snapshot(CACHE_DIR, d.market, ticker, d.price_history)`
   (best-effort,不影響回傳值);讀路徑零改動,函數註解寫明禁止增量的兩個原因。
5. 新增 `tests/test_history_store.py`:store CRUD/冪等/損毀退化、URL router 驗證
   全量/增量 start_date、拆股跨兩次增量 vs 一次性全量比對(逐值相等)、除息不誤判
   跨增量、history_years 加深回填、method 切換增補 PER、窗切片、US 快照 DELETE+INSERT、
   既有 99 題原封跑過。mock 紀律沿用 005(CACHE_DIR patch + urlopen 保險絲 + `_http_get_json`
   router),不依賴 test_providers_fallback.py(獨立成一份,避免跨測試檔耦合)。
6. Gate:py_compile → 完整測試 → mutation 重演(「永遠全量」「還原移到存入前」)→
   `python monitor.py report --ticker 2330` 黃金值連跑兩次(第二次應為 TW EOD-aware
   blob 快取 hit,history_store 不再被觸及)→ 更新 `docs/api-budget.md`。

## REPORT(executor 填)

### history_store 公開介面(最終,與 PLAN 草案一致,無偏離)

```python
# aimonitor/history_store.py —— 純儲存層,不 import providers(避免循環 import)。
# 全部函數第一參數顯式 cache_dir:str;呼叫端(providers.py)傳自己的模組全域
# CACHE_DIR(late-binding,測試 patch.object(providers,"CACHE_DIR",tmp) 時
# providers.py 內的參照自然跟著變)。全部「永不炸」:例外一律在函數內部吞掉。

upsert_price(cache_dir, market, ticker, rows:[(date,close)]) -> bool
upsert_per(cache_dir, market, ticker, rows:[(date,per,dividend_yield)]) -> bool
upsert_dividend(cache_dir, market, ticker, rows:[(ex_date,cash)]) -> bool
get_price(cache_dir, market, ticker, start_date=None) -> list|None   # None=不可用,[]=真的無資料
get_per(cache_dir, market, ticker, start_date=None) -> list|None
get_dividend(cache_dir, market, ticker, start_date=None) -> list|None
max_date(cache_dir, market, ticker, dataset:"price"|"per"|"dividend") -> str|None
get_meta(cache_dir, market, ticker, dataset) -> {"requested_start","last_success"}|None
set_meta(cache_dir, market, ticker, dataset, requested_start, last_success) -> bool
replace_us_snapshot(cache_dir, market, ticker, rows) -> bool   # DELETE+INSERT 同一交易
```

儲存:`<cache_dir>/history.sqlite3`,stdlib `sqlite3`、WAL、每次公開函數呼叫各開一條
`with` 包裹、用完即關的獨立連線;`_ensure_schema` 每次連線都跑一次(`CREATE TABLE IF
NOT EXISTS`,冪等)。四張表照 SPEC D1 逐字建:`daily_price(market,ticker,date,close)`、
`daily_per(market,ticker,date,per,dividend_yield)`、`dividend(market,ticker,ex_date,cash)`、
`series_meta(market,ticker,dataset,requested_start,last_success)`,PK 皆如 SPEC。

實測(scratchpad smoke script,見下方)確認 sqlite3.connect 對垃圾 bytes 檔案在
`PRAGMA journal_mode=WAL`/`CREATE TABLE`時才炸 `DatabaseError`,被每個公開函數的
try/except 接住,讀回 `None`、寫回 `False`,不外洩。

### fetch_tw 接線 diff 摘要(`aimonitor/providers.py`,+122/-24 行)

1. import 新增 `from . import history_store`(不循環,history_store 不 import providers)。
2. 新增私有 helper `_sync_and_assemble(dataset, cache_dir, ticker, requested_start,
   fetch_fn, upsert_fn, get_fn)`:讀 `series_meta` 判斷全量(無記錄,或
   `requested_start < meta.requested_start`)vs 增量(`start = history_store.max_date(...)
   or requested_start`)→ 呼叫 `fetch_fn(start)`(例外原樣往外拋,不吞)→
   upsert(best-effort)→ `set_meta`(best-effort)→ 從 store 取
   `date>=requested_start` 組裝序列;store 不可用(`None`)→ 退回這次剛抓到的
   `raw_rows`。**刻意不在這裡呼叫 `_back_adjust_tw`**。
3. `fetch_tw`:price/per/dividend 三段各自把「打 `_finmind` + 解析」包成內部
   closure(`_fetch_price`/`_fetch_per`/`_fetch_dividend`),傳給
   `_sync_and_assemble` 執行,取代原本直接呼叫 `_finmind(...)`。**`_back_adjust_tw`
   呼叫時機從『組裝完成前』移到『price_history/div_history 都組裝完成之後』**——
   這是本工單命脈(見下方 mutation B)。price 例外仍在最外層 try 捕捉、402 訊息
   逐字保留(未改動任一字)、直接 `return d`,不嘗試從 store 救援(SPEC D2 point 3
   「增量失敗→整個 fetch_tw 回傳 error(現狀行為)」)。PER/配息仍是
   `except Exception: pass`(非致命,現狀不變)。PER 的 `dividend_yield` 存
   FinMind 原始百分數(不在存入時 /100),轉換留到組裝後(比照原本只在
   `last row` 才轉換的語意)。
4. `fetch_us`/`fetch_us_finnhub`:`d.price_history` 組出來的當下,追加呼叫
   `history_store.replace_us_snapshot(CACHE_DIR, d.market, ticker, d.price_history)`
   (best-effort,不影響回傳值);函數內加註解說明禁止增量的兩個原因
   (auto_adjust 回溯改寫 + INTL total-return 語意)。**讀路徑(`d.xxx` 欄位的
   計算邏輯)一行未改**。

**禁區確認(git diff 逐行核對)**:`_load_cache`/`_load_cache_raw`/`_save_cache`/
`_tw_cache_fresh`/`_cache_age_minutes`/`_parse_fetched_at`/`_now_tpe`/`_tw_eod_hour`/
`fetch()`(統一入口,含 stale-rescue 段)全部**不在 diff 內、零改動**;
`_back_adjust_tw` 函數本體**不在 diff 內、零改動**(只有呼叫時機挪動,呼叫的
仍是同一個函數物件);US 的 `d.price`/`d.price_date`/`d.trailing_eps`/`info` 相關
計算行**零改動**,只新增了一行 side-effect 呼叫。

### 測試

新檔 `tests/test_history_store.py`:**29 題**,涵蓋 SPEC D4 全部 8 點
(store CRUD/冪等 11 題、損毀退化 9 題、store 不可用時 fetch_tw 仍成功 1 題、
增量 start_date 鑑別 1 題、拆股跨增量 1 題、除息跨增量不誤判 1 題、
history_years 加深回填 1 題、method 切換增補/複用 PER 1 題、窗切片 1 題、
US 快照 DELETE+INSERT 1 題、fetch_tw 層級呼叫數 regression 1 題)。

```
python -m py_compile aimonitor/providers.py aimonitor/history_store.py \
    tests/test_history_store.py monitor.py app.py
PY_COMPILE_ALL_OK

python -m unittest tests.test_history_store -v
Ran 29 tests in 1.615s
OK

python -m unittest discover -s tests
Ran 128 tests in 2.212s
OK   # 99(既有,原封不動)+ 29(新增),全綠
```

mock 紀律沿用工單 005(CACHE_DIR 隔離 tempdir、urlopen 保險絲、`_http_get_json`
逐條 mock、sleep no-op、環境變數快照),獨立寫一份基底類別,不 import
`test_providers_fallback.py`(避免測試檔互相耦合)。

### Mutation 重演(記憶體 monkeypatch,腳本見 scratchpad,未改任何檔案)

**Mutation A:「退化成永遠全量」**——`history_store.get_meta` monkeypatch 成恆回傳
`None`(模擬全量/增量判斷失效)。

```
=== STEP 1: apply mutation, run discriminating test ===
test_second_fetch_start_date_is_store_max_date_not_window_start ... FAIL
AssertionError: '2021-08-09' != '2021-08-10'
mutation A run -> tests: 1 failures: 1 errors: 0 (expect >=1 failure)

=== STEP 2: revert mutation, re-run FULL suite ===
full suite after revert A -> ran: 128 failures: 0 errors: 0
```
(第二次呼叫的 start_date 變回窗起點 `2021-08-09`,而不是預期的 store MAX(date)
`2021-08-10`——精準對應「永遠全量」這個突變的可觀察後果。)

**Mutation B:「還原時機移到存入前」**——`history_store.upsert_price` monkeypatch
成「先對這次剛抓到的 `raw_rows` 局部呼叫 `_back_adjust_tw(rows, [])` 再存入」,
同時把 `providers._back_adjust_tw`(`fetch_tw` 最後呼叫的那個)monkeypatch 成
no-op passthrough(模擬整段還原邏輯搬家,而非多做一次)。

```
=== STEP 1: apply mutation, run discriminating test ===
test_two_increments_matches_one_shot_full_fetch ... FAIL
AssertionError: Lists differ:
  one_shot      = [('2021-08-09', 99.5049504950495), ('2021-08-10', 100.5), ...]
  two_increment = [('2021-08-09', 400.0),             ('2021-08-10', 100.5), ...]
First differing element 0: ('2021-08-09', 99.50...) != ('2021-08-09', 400.0)
mutation B run -> tests: 1 failures: 1 errors: 0 (expect >=1 failure)

=== STEP 2: revert mutation, re-run FULL suite ===
full suite after revert B -> ran: 128 failures: 0 errors: 0
```
解讀:one-shot 場景即使在突變下仍正確還原(單一批次看得到全部 4 筆,局部處理
剛好等於正確處理),但 two-increment 場景第一批(僅 2 筆,局部看不出未來的
分割)存入 store 的是**未還原**的 `400.0`,第二批的局部還原無法回頭修正——
與我事前推演的失效模式完全吻合,證明 D2 point 4「還原必須在組裝完整序列之後
做一次」是真正被鎖住的行為,不是巧合通過。

`MUTATION REPLAY OK: both mutations caught, full suite green after each revert.`

### 黃金值交叉(真實 FinMind,2 次呼叫:pe_band = Price+PER)

```
python monitor.py report --ticker 2330
  便宜價   NT$2,228.57
  大特價   NT$1,731.23
```
與黃金值精確相符(PDF ≈2,226/≈1,729)。緊接著**第二次**執行同指令,輸出逐字
相同(現價、五價格帶、隱含 PE、波動率、百分位全部一致)——命中 TW EOD-aware
blob 快取(工單 013,`.cache/TW_2330.json` 同日新鮮),0 額外 FinMind 呼叫。

實測順帶驗證了 history_store 的真實運作:`.cache/history.sqlite3` 產生,
`get_meta(...,"price")`/`get_meta(...,"per")` 皆為
`{"requested_start": "2021-08-09", "last_success": "2026-08-19T09:32:53..."}`
(5 年窗,首次全量),price/per 各 1222 筆,`price_history`/`per_history` 最後
3 筆日期與現價/PER 皆與 CLI 輸出一致。

### 新增 API 呼叫評估

**0 新增**。呼叫次數不變(S8 契約:price_band=1/pe_band=2/yield_band=2,新增
`CallCountAtFetchTwLevelTest` 在 `fetch_tw` 直接呼叫層級也鎖了一次);改變的
只有「blob 快取過期後例行重抓」時單次呼叫的 payload 大小(整段 N 年 → 上次
之後的增量)。黃金值交叉驗收花費 2 次 FinMind(在 SPEC 允許的 1–2 次之內)。

### 剩餘風險

1. **`_sync_and_assemble` 對 market 寫死 `"TW"`**:設計上此 helper 只服務
   `fetch_tw`(SPEC D2 明文「僅台股增量」),寫死換取簽名精簡;若未來要給
   其他市場複用增量邏輯,需要加 `market` 參數,屬於刻意的範圍限縮,非疏漏。
2. **`ExDividendAcrossIncrementsTest` 對「還原時機」突變的鑑別力較弱**
   (測試 docstring 已誠實註記):此案例的跳空與 ex_date 同時落在第二批次內,
   即使錯誤地把還原時機搬到「各批次局部處理」,也可能局部就正確排除,不會
   翻紅。真正對這個突變有強鑑別力的是 `SplitAcrossIncrementsTest`(已在
   mutation B 證實)。這條測試驗證的是另一個獨立面向(ex_date 排除邏輯經過
   store 組裝路徑後依然正確),予以保留。
3. **`series_meta.last_success` 目前只是時間戳記錄,尚無讀取端消費者**——
   為 015(缺日偵測/品質警告)預留的欄位,本工單未使用它做任何判斷邏輯,
   純粹忠實依 SPEC D1 schema 寫入。
4. **WAL 模式的 sidecar 檔案(`-wal`/`-shm`)**:多次真實/測試連線後未見殘留
   (每次操作用完即關,SQLite 在無其他連線持有時通常會自動 checkpoint),
   但沒有針對「WAL sidecar 在極端中斷情境下殘留」寫專門測試——影響範圍僅止
   於本地磁碟用量與下次開啟時的自動復原(sqlite3 標準行為),不影響本工單
   的資料正確性結論。
5. **未跑 `streamlit run app.py` 冒煙**:本工單允許檔案清單不含 `app.py`,
   实際上也沒有修改它;`app.py` 對 `aimonitor.providers.fetch()` 的呼叫介面
   (函數簽名、回傳的 `StockData` 欄位)全部未變,風險評估為低,但這是
   「未執行」而非「已驗證」,如需更高把握建議 orchestrator 或後續工單補跑。

---

## REVIEWER 修正包(executor 執行,無 commit;白名單不變:providers.py、
history_store.py、tests/test_history_store.py、docs/api-budget.md、工單檔)

Reviewer 找到兩條 P1(皆有真實重現)+ 多條 P2/P3。逐項處理如下,紅線
(blob/EOD/rescue 零觸碰、`_back_adjust_tw` 本體零改動、S8 呼叫數不變、
0 新增 API)全程遵守。

### F1(P1-1 根修,最優先):sqlite 可讀不可寫時的合併保底

**問題**:舊設計 upsert 後直接信任 `get_fn` 讀回結果——sqlite 可讀不可寫時
(reviewer 用真實檔案鎖重現),讀回的是「寫入前的舊資料」而不自知,導致
FinMind 明明成功卻回傳空/缺新資料的 StockData、`error` 空字串、`fetch()`
不寫回 blob 快取(`if data.ok(): _save_cache(...)`)→ 下一輪仍是 cache miss,
額度保護整個失效。

**修法**:`_sync_and_assemble` 改為**無條件在記憶體合併**——新增
`_merge_rows_by_key(base_rows, fresh_rows)`,以每筆 tuple 第一欄(日期)為鍵,
`fresh_rows`(這次剛抓到的原始資料)覆蓋 `base_rows`(`get_fn(...) or []`,
store 讀到的既有資料)同鍵的值,回傳依鍵升冪排序的完整列表。store 全好時
合併是冪等無感(讀回本來就已含這次寫入的資料);store 寫入失敗時,靠這次
記憶體裡的 `raw_rows` 補位,fetch 仍然成功、資料仍然新鮮,只是這次的
upsert 沒有被「下一次呼叫的增量」撿到而已。同時 `history_store._connect`
的 `timeout` 5→2(reviewer 實測單 dataset 最差可阻塞到 10.1s,降到 ~4s 上限,
讓「store 暫時不可用」更快 fail fast、交給合併保護接手)。

**新增測試**:`WriteFailsButReadWorksStillMergesFreshDataTest`——
`patch.object(history_store, "upsert_price"/"upsert_per", return_value=False)`
恆回 `False`,store 用真正(未被 patch)的函數預先灌入半年前的舊列,method
="pe_band" 增量抓到全新一批(含重疊列)。斷言:`result.ok()` 為 True、
`price`/`per` 反映最新一筆、`price_history`/(間接經由 `per`)同時含舊列與
新列、store 內確認新資料真的沒被寫入(佐證 upsert 真的失敗了,不是巧合
成功)。

### F2(P1-2a):價格增量回應為空 → 視為失敗

**問題**:增量起點 = store 既有序列的 `MAX(date)`(含當天),正常情況下
FinMind 一定至少會回傳這筆重疊列——回空屬異常(暫時性資料源問題等),不是
「這段期間真的沒有交易」。舊設計會把這個異常悄悄吞掉(合併後只剩 store 的
舊資料,`d.ok()` 仍是 True,看似成功實則吞掉了一次異常)。

**修法**:`_sync_and_assemble` 回傳 `(merged_rows, incremental_empty)` 二元組,
`incremental_empty = (走增量分支) and (raw_rows 為空)`。`fetch_tw` 的價格段
檢查此旗標,若為 True → 設 `d.error = "FinMind 增量回應為空(無新資料),
沿用快取。"` 並 `return d`(402 分支與訊息逐字不動),讓 `fetch()` 既有的
stale-rescue 接手(標「(過期快取)」,恢復 014 之前的失敗語意)。**僅
「增量」回空才算異常**;全量抓取回空維持現狀行為(新上市無資料等)。

**新增測試**:
- `IncrementalEmptyPriceResponseIsTreatedAsFailureTest`(2 題):store 有
  半年前資料、增量回空 → `ok()==False`、`error` 非空且提及「增量」;對照組
  (全量回空,無 meta)→ `ok()==False` 但不含這則訊息(不受 F2 影響)。
- `IncrementalEmptyPriceResponseTriggersStaleRescueAtFetchLevelTest`:
  在 `fetch()` 層級驗證——4 天前的過期 blob 快取 + store 有舊資料(增量
  分支)+ 這次回空 → `fetch()` 回傳 `ok()==True`、`source` 含
  「(過期快取)」(stale-rescue 接手成功)。

### F3(P1-2b 護欄):PER 派生新鮮度門檻

**問題**:PER 的「最新一筆」現在來自 store 組裝,可能是先前某次增量留下的,
不再保證跟這次的 `d.price_date` 出自同一批 API 回應(014 之前 PER 跟價格
永遠同一次呼叫抓回,天然沒有這個落差)。reviewer 的 S1 情境:PER 停在半年
前、price 是今天,若仍派生 `d.per`/`d.trailing_eps`/`d.dividend_yield`,
算出的隱含估值會嚴重誤導。

**修法**:新增 `_days_between(a, b)` 純函數,`fetch_tw` 的 pe_band 段落只有
當 `per_rows[-1]` 的日期與 `d.price_date` 相差 <=10 天才派生三個欄位,否則
維持 `None`(`d.per_history` 河流圖序列不受此限制,仍是完整歷史序列)。

**新增/補強測試**:
- `PerDerivationFreshnessGuardTest`(2 題):PER 落後 price 約半年 → 三個
  派生欄位皆 `None`(`per_history` 仍含該筆歷史資料);對照組(落後 3 天)
  → 正常派生。
- `MethodSwitchAddsPerDatasetTest`:補上第二、三次呼叫的
  `per`/`dividend_yield`/`trailing_eps` 派生斷言(先前只驗證呼叫次數/URL,
  親手造出的 0~1 天小幅時間差從未斷言過三個派生欄位)。
- 新增 `MethodFullRoundTripPeBandThenPriceBandThenPeBandTest`:SPEC D4-4
  完整回切 pe_band→price_band→pe_band——第二次(price_band)完全不碰
  `TaiwanStockPER`(mock 內故意 `raise AssertionError` 驗證這件事);per 的
  `series_meta` 在第一次之後沒被動過;第三次(切回 pe_band)per 走增量
  (`start_date` = store MAX(date),不是窗起點)且派生正確。

### F4(P2-1/P2-2 一次消滅):配息撤出增量

**問題**:FinMind `TaiwanStockDividend` 的 `start_date` 篩的是「公告日」
欄位,不是 `CashExDividendTradingDate`(除息日)——兩者可能相差數月甚至
跨年,拿「上次看到的除息日」當增量游標語意對不上真正的篩選欄位,可能漏抓
「公告在游標之前、但除息日還沒發生」的紀錄,或被未來 ex_date 卡住游標。
另外 store 的 dividend 表 PK 是 `(market,ticker,ex_date)`,若同一 ex_date
曾有多筆紀錄(真實資料可能發生),upsert 會把現狀的 SUM 語意破壞成
last-wins。兩者相加,對配息做增量是「零收益、有風險」。

**修法**:`fetch_tw` 的 `yield_band` 段落完全撤出 `_sync_and_assemble`,
逐字恢復 014 之前的原始碼路徑——每次都全量抓(`start=start`,仍是 1 次
`_finmind` 呼叫),`d.div_history` 直接對這次全量抓到的 raw 資料排序組裝,
不經 store 讀取。`history_store.upsert_dividend` 仍保留呼叫(best-effort
耐久備份,供未來工單使用),但**不參與**這次的 `d.div_history`/
`d.annual_dividend` 組裝。

**測試改寫/新增**:
- `ExDividendAcrossPriceIncrementsWithAlwaysFullDividendTest`(原
  `ExDividendAcrossIncrementsTest` 改名+補強):價格仍跨兩次增量、配息兩次
  呼叫都補上 `start_date == 窗起點` 的斷言(證明恆全量,不是遞增的
  store MAX(ex_date))。
- 新增 `DividendAlwaysFullNeverIncrementalTest`:連續兩次 `fetch_tw` 呼叫,
  兩次配息 `start_date` 皆為窗起點。
- 新增 `DuplicateExDateDividendSumPreservedTest`:同一 ex_date 兩筆紀錄
  (現金股利+法定盈餘公積分開兩列)→ `d.div_history` 保留兩筆(不摺疊)、
  `d.annual_dividend` 正確 SUM。
- 新增 `Yield0056StyleOfflineParityWithPre014Test`(對應 Gate 要求的「另挑
  一檔 yield_band(如 0056)不實跑」):合成 13 筆模擬 0056 型高頻配息(跨
  365 天 cutoff 邊界、含重複 ex_date),與獨立手算 oracle(逐字比照 014
  之前=F4 之後的原始邏輯)逐位比對 `div_history`/`annual_dividend`;連續
  呼叫兩次確認 `start_date` 皆為窗起點(恆全量)。S8 呼叫數(yield_band=2)
  不受影響。

### F5(測試假陰性 + 未來鋪路)

(a) `_ensure_schema` 加 `conn.execute("PRAGMA user_version=1")`(純粹為未來
migration 鋪路,無其他邏輯分支)。
(b) `WindowSlicingTest` 補 `self.assertTrue(result.price_history)`——先前
只用 `all(d >= window_start for d,_ in result.price_history)`,對空序列
`all()` 恆真,沒有明確的非空斷言。
(c) `HistoryYearsDeepensTriggersBackfillTest` 補
`self.assertIn("2016-08-10", [d for d,_ in result.price_history])`——先前
只驗證 URL/meta,沒有驗證「回傳給呼叫端的序列」真的含有加深後才抓到的更早
日期資料。

### F6(文件誠實化):`docs/api-budget.md` §3 第 5 點大幅重寫

新增/更新內容對照 reviewer 逐點要求:寫失敗安全(F1 後為真且有測試佐證)、
配息不做增量與兩個具體理由、增量的固有代價(歷史值凍結,FinMind 事後
更正不再自動吸收;自癒手段=清快取或 history_years 調大再調回;定期全量
重同步列 backlog)、`use_cache=False` 只繞過 blob 新鮮度不繞過 store(store
是耐久資料層非新鮮度快取,現價新鮮度仍由這次真的打 live API 保證)、美股
殘留全量重抓正面陳述(不是漏做優化,是正確性優先的刻意設計,美股額度風險
本來就遠低於台股)、美股快照寫入成本(每次成功 ~1250 列 DELETE+INSERT,
純本機操作,對耗時無感)。

### Gate

```
python -m py_compile aimonitor/providers.py aimonitor/history_store.py \
    tests/test_history_store.py monitor.py app.py
PY_COMPILE_ALL_OK

python -m unittest discover -s tests
Ran 138 tests in 4.695s
OK   # 99(既有,原封不動)+ 39(test_history_store.py,原 29 + 本輪新增 10 題)
```

`git diff --stat -- tests/` 對既有 7 個測試檔仍是空 diff(byte-for-byte
未動);`git status --porcelain` 確認白名單外無檔案異動。

### Mutation 重演(記憶體 monkeypatch,腳本見 scratchpad,未改任何檔案;
三種:A、B(含 F1 後的鑑別力再驗證與替代版本 B')、C(F1 新增))

**Mutation A**(不變,`history_store.get_meta` 恆回傳 `None`):
```
test_second_fetch_start_date_is_store_max_date_not_window_start ... FAIL
AssertionError: '2021-08-09' != '2021-08-10'
mutation A run -> tests: 1 failures: 1 errors: 0
full suite after revert A -> ran: 138 failures: 0 errors: 0
```

**Mutation B**(原始重演腳本,`upsert_price` 局部 back_adjust + 最終
`_back_adjust_tw` 中和):F1 合併重構後**誠實記錄一個發現**——原本的主斷言
`assertEqual(one_shot.price_history, two_increment.price_history)` 這次
**沒有**翻紅(兩邊都算出同一組「未還原」的原始值,因為 F1 的合併規則是
「這次剛抓到的 `raw_rows` 一律覆蓋對應日期」,對「這次呼叫涵蓋的日期範圍」
而言,無論 store 裡先前被局部 mutate 寫入什麼,合併後都會被這次的
`raw_rows`——也就是純原始、未受 upsert-mutation 影響的值——蓋回去;
one-shot 與 two-increment 兩邊各自「覆蓋掉的範圍」剛好互補,最終都收斂成
同一組原始序列)。真正翻紅的是最後一條「非退化」防線:
```
test_two_increments_matches_one_shot_full_fetch ... FAIL
AssertionError: 400.0 == 400.0
  (assertNotEqual(two_increment.price_history[0][1], self.FULL_RAW[0][1]) 失敗)
mutation B run -> tests: 1 failures: 1 errors: 0
full suite after revert B -> ran: 138 failures: 0 errors: 0
```
即:mutation B 仍然「被抓到」(1 failure),但鑑別機制從「一次性 vs 跨增量
不一致」位移到「還原有沒有真的發生」。這個位移本身就是 F1 合併設計的
一個誠實的副作用,記錄於此、不隱藏。

**Mutation B'(本輪新增,對 F1 之後更直接/更貼近 D2 point 4 語意的替代版)**:
把 `_back_adjust_tw` 的「視野」限縮成只看得到 `fetch_fn` 這一批剛抓到的
`raw_rows`(看不到 store 裡更早的資料),而不是像正確實作一樣對『合併完的
完整序列』做一次(`_sync_and_assemble` 包一層 scoped fetch_fn,並中和
`fetch_tw` 最終的 `_back_adjust_tw` 呼叫):
```
test_two_increments_matches_one_shot_full_fetch ... FAIL
AssertionError: Lists differ:
  one_shot      = [('2021-08-09', 99.5049504950495), ('2021-08-10', 100.5), ...]
  two_increment = [('2021-08-09', 400.0),             ('2021-08-10', 100.5), ...]
mutation B' run -> tests: 1 failures: 1 errors: 0
full suite after revert B' -> ran: 138 failures: 0 errors: 0
```
這次是透過**主斷言**(`one_shot.price_history == two_increment.price_history`)
直接翻紅,更乾淨地重現「還原視野被局限在單一批次」這個 D2 point 4 要防的
核心錯誤,不受 F1 合併機制的覆蓋規則影響。**建議**:往後若要再驗證這條
命脈,以 B' 的手法(限縮 `_back_adjust_tw` 的輸入視野)為準;原始 B 的
「local-adjust-before-upsert」手法在 F1 之後鑑別力已轉移到次要斷言,不再
是最直接的證據來源,但因為它仍然「有」鑑別力(只是走的路徑變了),沒有
從測試中移除既有斷言。

**Mutation C(F1 新增)**:`_merge_rows_by_key` monkeypatch 成
`lambda base, fresh: base`(合併退化成只回傳 store 讀到的既有資料,完全
忽略這次剛抓到的新資料)。
```
test_upsert_always_fails_fetch_still_succeeds_with_merged_fresh_and_old_rows ... FAIL
AssertionError: 350.0 != 400.0
mutation C run -> tests: 1 failures: 1 errors: 0
full suite after revert C -> ran: 138 failures: 0 errors: 0
```

`MUTATION REPLAY OK (A, B', C): all three mutations caught, full suite green after each revert.`

### 黃金值交叉(reviewer 修正包後重跑,真實 FinMind,2 次呼叫:pe_band=Price+PER)

```
python monitor.py report --ticker 2330
  便宜價   NT$2,228.57
  大特價   NT$1,731.23
```
與黃金值精確相符(PDF ≈2,226/≈1,729)。`.cache/TW_2330.json` 的 `_fetched_at`
確認從修正包前的 `2026-08-19T09:32:53` 更新到修正包後的
`2026-08-19T10:28:44`(跨過台北 18:00 EOD 邊界後的即時重抓,證明 F1–F4 改動
沒有破壞 TW EOD-aware 快取層與黃金值管線),緊接著第二次執行同指令,輸出
逐字相同(命中新寫入的 blob 快取)。

### 新增 API 呼叫評估

**0 新增**。所有 F1–F4 改動都在「同一次呼叫內部怎麼判斷/組裝/派生」的邏輯
層,call 站台(`_finmind` 呼叫次數)完全不變:`CallCountAtFetchTwLevelTest`
(price_band=1/pe_band=2/yield_band=2)與新增的
`Yield0056StyleOfflineParityWithPre014Test` 皆再次確認。黃金值交叉驗收花費
2 次 FinMind(在 SPEC 允許的 1–2 次之內)。

### 剩餘風險(reviewer 修正包後)

1. **Mutation B 的鑑別路徑位移**(見上方 mutation 重演小節)——已誠實記錄,
   不算新風險,但提醒未來維護者:如果只看「主斷言是否翻紅」可能誤判鑑別力
   消失,務必連同 `assertNotEqual` 那條退化防線一起看,或改用 B' 手法。
2. **F1 合併規則的邊界情況未窮舉**:目前的合併是「這次的 `raw_rows` 對它
   涵蓋的日期範圍整段覆蓋 store 讀回結果」,還沒有測過「這次增量的
   `raw_rows` 本身就帶有錯誤/髒資料」的情境(不在本次修正包範圍,FinMind
   回應內容本身的資料品質不是這裡的責任範圍)。
3. **F3 的 10 天門檻是經驗值,非公式推導**:reviewer 沒有指定精確數字,
   10 天是「大幅寬於一般連續交易日」的保守選擇,足以吸收正常的增量步調
   落差,同時能攔住 reviewer S1 情境的半年落差。如果未來出現「PER 剛好
   卡在 8~10 天」的邊緣情況,可能需要重新校準,目前無測試覆蓋這個精確
   邊界值(D4 系列測試沒有專門鎖 10 天這個數字本身,只鎖「明顯新鮮」與
   「明顯過期」兩種情境)。
4. 前一輪 REPORT 記錄的 5 條剩餘風險(`_sync_and_assemble` market 寫死
   "TW"、`ExDividendAcrossIncrementsTest` 原始鑑別力誠實註記、
   `series_meta.last_success` 尚無讀取端消費者、WAL sidecar 未測、未跑
   streamlit 冒煙)維持原判斷不變,不重複列出。
