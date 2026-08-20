# 020 — 測試套件日期穩定性(緊急;擋 018 收斂)

狀態:**CLOSED**(2026-08-20,commit 見文末收斂紀錄)

## 收斂紀錄(orchestrator)

- 修紅 2 題(014 的拆股/除息跨增量 fixture 動態化)+ 拆彈 2 顆:
  WindowSlicingTest(**明日 8/21 即爆**,壓線日期)與 TTM cutoff 測試
  (**2027-01-01 爆**,掃雷發現最急迫者)+ 4 處 ~2031 遠期順手修。
- 全 11 測試檔判定表入 REPORT:test_history_store 19 class(16 永不炸)、
  test_twse_fallback 14 class、eod/monthrev/其餘檔全數永不炸或結構免疫。
- **延後 4 題(~2031-08-26)**:DerivedFieldsAndG1FallbackTest 的 F3 邊界組,
  因 store 日期與寫死民國 `roc_date` 有精確天數差語意,單改一邊會把遠期彈
  變成「明日漂移」——正確修法需動態民國日期共用 helper,併入 016 backlog。
- 防再犯慣例已入 test_history_store 檔頭 docstring。
- 收斂 gate:277/277 綠(orchestrator 親跑 ×1、executor ×2)、py_compile 綠、
  aimonitor/ 零觸碰、0 API。免 reviewer(純測試 fixture 日期化,判定表可逐列
  複核,orchestrator 已抽驗根因與 WindowSlicing 壓線推算)。→ CLOSE。

## 現象與根因(orchestrator 已定性)
2026-08-20 起 `test_history_store.py` 兩題翻紅(SplitAcrossIncrementsTest、
ExDividendAcrossPriceIncrementsWithAlwaysFullDividendTest):`fetch_tw` 的
`requested_start = now − (years×365.25+10)天` 隨真實日期每日前移;fixture 寫死
`2021-08-09..12`,昨日窗起點 2021-08-09(含首列)今日 2021-08-10(切掉首列),
組裝切片與「全序列 oracle」出現差一列。**引擎行為正確,是測試 oracle 沒對齊
滑動窗**——寫下當天就注定未來某日會炸的日期炸彈。

## SPEC
1. **修紅**:兩個失敗 class 的 fixture 改為**相對今日**的近期日期(如
   now−20..now−17 天,用 `datetime.now()` 於 setUp 生成字串),使其永遠深居
   任何 years≥1 的窗內;oracle 同步以同一組動態 fixture 計算。注意 015 的
   quality_warnings 會因尾端 >10 天附掛——斷言只比 price_history/price,
   不受影響(確認即可,若有斷言碰到 warnings 需同步調整)。
2. **全面掃雷**:審視 tests/ 全部檔案,列出每一個「寫死日期 fixture ×
   now 派生窗/時鐘」的交互點,逐一判定:已炸/未來會炸(算出引爆日)/永不炸
   (附理由,如全程假時鐘、或斷言不涉窗切片)。判定表寫進 REPORT。
   會炸者一併改為相對日期或窗對齊 oracle;用假時鐘(_now_tpe patch)者優先
   維持假時鐘(決定性最好)。
3. **防再犯**:tests/ 新增(或於 README-of-tests docstring 註記)慣例一句:
   「fixture 日期若與 now 派生的窗互動,必須用相對日期或釘假時鐘」。

## 驗收
- `python -m unittest discover -s tests` 全綠(277);把系統日期心算前推 1 天/
  1 年(不真改系統時間,以窗算式紙上驗證)確認判定表結論;py_compile。
- 0 API;不改 aimonitor/ 任何檔。

## PLAN(executor 填)

1. 讀 `tests/test_history_store.py` 兩個失敗 class(`SplitAcrossIncrementsTest`、
   `ExDividendAcrossPriceIncrementsWithAlwaysFullDividendTest`)+ `aimonitor/providers.py`
   的 `_sync_and_assemble`/`fetch_tw`/`_assemble_tw_fallback`,對照
   `aimonitor/history_store.py` 的 `get_price`/`get_per`/`get_dividend`(`date>=?`
   篩選、`max_date` 不篩選)確認根因細節:store 讀回會被 `requested_start`(隨
   `datetime.now()` 每天前移)篩掉早期列,但「這次剛抓到的 raw_rows」不受篩選
   ——只有「前一次呼叫寫入 store 的列,這一次呼叫的 raw_rows 沒有重新提供」時
   才會真的漏資料。
2. 修紅:兩個 class 的 `FULL_RAW`(+`DIV`)從類別屬性改成 `setUp` 內用
   `datetime.now()-timedelta(days=20..17)` 動態生成的實例屬性;oracle
   (`providers._back_adjust_tw(self.FULL_RAW, [])`)已經引用 `self.FULL_RAW`,
   無需另外改。
3. 全面掃雷:先跑一次全套(修正前)確認「今天(2026-08-20)只有這 2 題紅」,
   再逐檔逐 class 用「(a) 有沒有 `history_store` 二次讀(store 讀回受窗篩選)
   跟『這次 raw_rows』內容是否一致」「(b) 斷言是否比較全序列/窗邊界,還是只看
   最後一列或呼叫次數」「(c) TTM 365 天 cutoff 是否用寫死日期」三個判準人工比對
   `tests/test_history_store.py`(全 19 個測試 class)、`test_eod_cache.py`、
   `test_twse_fallback.py`、`test_month_revenue_guardrail.py`,並抽查
   `test_providers_fallback.py`/`test_quality_warnings.py`/`test_golden_valuation.py`
   等其餘檔案(grep 確認完全不碰 `history_store`/`fetch_tw`,結構性免疫)。
4. 會炸的一併修(見下方判定表);其中 `test_twse_fallback.py::DerivedFieldsAndG1FallbackTest`
   4 題因為需要同步改「動態產生民國日期字串」才能修好又不破壞既有的
   F3 天數邊界語意,风险/效益不對稱,列為「未來會炸(~2031-08-26,低急迫度)」
   留待未來工單,本工單不動(見下方風險段落)。
5. 防再犯:`tests/test_history_store.py` 檔頭 docstring 加慣例段落。
6. Gate:`python -m unittest discover -s tests`(277)+ `python -m py_compile`
   改到的兩個檔。0 API(全程沒有執行任何會打網路的指令,含黃金值 CLI 對照
   ——本工單未動 `aimonitor/`,估價/分類數值不受影響,略過 `monitor.py report`
   人工黃金值比對)。

## REPORT(executor 填)

### 修了哪些 class(diff 摘要)

**`tests/test_history_store.py`**(檔頭 docstring 加一段「防再犯」慣例說明,另 3 處):
- `SplitAcrossIncrementsTest`(原始紅):`FULL_RAW` 類別屬性 → `setUp` 內用
  `datetime.now()-timedelta(days=20..17)` 動態生成的實例屬性(4 個連續日期)。
- `ExDividendAcrossPriceIncrementsWithAlwaysFullDividendTest`(原始紅):同上,
  `FULL_RAW`+`DIV` 一併動態化,`DIV` 的除息日沿用 `FULL_RAW[2]`(跳空日)同一個
  動態日期,維持「跳空當日除息」的原始語意不變。
- `WindowSlicingTest.test_shallower_request_slices_to_requested_window_not_full_store_depth`
  (**未在原始紅清單、但「明天」2026-08-21 就會翻紅**,見下方判定表詳解):
  `in_window_date` 從寫死 `"2021-08-10"` 改成 `datetime.now()-timedelta(days=20)`。
  `very_old_date="2010-01-01"` 維持寫死不動(理由見判定表,窗起點單調前移、
  一旦排除永遠排除,無風險)。

**`tests/test_twse_fallback.py`**(4 處,全部屬於「~2031 遠期會炸」但修法簡單
不牽涉 roc_date 協調,順手一併修掉):
- `HistoryStoreAssemblyAndClassifyWiringTest.test_history_store_has_data_gets_assembled_into_fallback_result`:
  store 種子日期(price ×2、dividend ×1)改為相對「現在」動態生成。
- `ScaleGuardTest` 兩題(`test_ratio_025_below_threshold_discards_all_three_series_with_warning`/
  `test_ratio_1_0_within_threshold_keeps_history`):store 種子日期改為
  `seed_date = datetime.now()-timedelta(days=20)`。
- `DerivedFieldsAndG1FallbackTest.test_annual_dividend_ttm_cutoff_only_sums_within_365_days`
  (**引爆日最近的一個,2027-01-01**,見判定表):price/兩個除息日全部改為相對
  「現在」動態生成(`days=20/200/800`),同時解決「5 年窗排除」與「365 天 TTM
  cutoff」兩個獨立風險。
- `DerivedFieldsAndG1FallbackTest.test_store_path_runs_back_adjust_tw_for_synthetic_split`:
  price 種子日期改為相對「現在」動態生成。

### 判定表(全檔逐一審視)

圖例:[已修/原始紅] 此工單修的原始紅題 / [永不炸] / [已修/未來會炸] 此工單順手修掉的未來風險 / [未修/未來會炸] 遠期風險、留待未來工單

**`tests/test_history_store.py`**(19 個測試 class,先跑過未修正版確認「今天
只有 2 題紅」,已在下方逐 class 列出理由):

| Class | 判定 | 理由 |
|---|---|---|
| `StoreCrudTest` | [永不炸] | 純 CRUD,不呼叫 `fetch_tw`,不碰 `datetime.now()` |
| `StoreCorruptionTest` | [永不炸] | 同上(損毀退化,一樣不碰 now） |
| `StoreUnavailableFallsBackToSuccessfulFetchTest` | [永不炸] | store 損毀→讀一律 `None`,結果完全來自這次 `raw_rows`(不受窗篩選) |
| `WriteFailsButReadWorksStillMergesFreshDataTest` | [永不炸] | `upsert` 被 patch 恆 `False`,但這次 `raw_rows`(mock 固定回傳新舊兩筆,不論 URL)本身就含舊列,merge 时永遠補回,不靠 store 篩選存活 |
| `IncrementalEmptyPriceResponseIsTreatedAsFailureTest` | [永不炸] | 只斷言 `ok()`/`error` 文字,無序列比較 |
| `IncrementalEmptyPriceResponseTriggersStaleRescueAtFetchLevelTest` | [永不炸] | blob 過期用相對 `timedelta(days=4)`,store meta 用動態 `_window_start(5)` |
| `IncrementalStartDateDiscriminationTest` | [永不炸] | 只斷言 URL `start_date`/呼叫次數,無序列比較 |
| `SplitAcrossIncrementsTest` | [已修/原始紅] | **原始紅**(根因:store 讀回被窗篩掉首列,2nd 次 raw_rows 不含該列) |
| `ExDividendAcrossPriceIncrementsWithAlwaysFullDividendTest` | [已修/原始紅] | **原始紅**(同根因) |
| `DividendAlwaysFullNeverIncrementalTest` | [永不炸] | 配息組裝完全不經 store 讀(F4 恆全量直接用 raw），price 用固定日期但只取最後一筆 |
| `DuplicateExDateDividendSumPreservedTest` | [永不炸] | 除息日已用 `now()-30天` 動態生成(既有慣例正確示範） |
| `Yield0056StyleOfflineParityWithPre014Test` | [永不炸] | 除息日已用 `now()-N天` 動態生成 |
| `HistoryYearsDeepensTriggersBackfillTest` | [永不炸] | 固定 `"2021-08-10"/"2016-08-10"`,但只用 `assertIn`(這次 raw_rows 單次全量涵蓋,不靠 store 篩選後留存） |
| `MethodSwitchAddsPerDatasetTest` | [永不炸] | 固定 PER 日期,但只斷言「最後一筆」衍生欄位(`.per`/`.dividend_yield`/`.trailing_eps`），不受更早列被篩掉影響 |
| `PerDerivationFreshnessGuardTest` | [永不炸] | 單次呼叫(store 原本是空的),`per_history` 全序列來自這次 raw_rows,不受窗篩選 |
| `MethodFullRoundTripPeBandThenPriceBandThenPeBandTest` | [永不炸] | 同 `MethodSwitchAddsPerDatasetTest`,只看最後一筆 |
| `WindowSlicingTest` | [已修/未來會炸] | **原本未紅,但明天(2026-08-21)就會翻紅**——`in_window_date="2021-08-10"` 剛好等於今天算出的 `window_start(5)`,`>=` 比較今天壓線通過,明天窗前移 1 天即超過,`assertTrue(all(d >= window_start_5y ...))` 翻紅（已用 `datetime.now()-timedelta(days=20)` 修正） |
| `UsSnapshotFullReplaceTest` | [永不炸] | 美股走 `replace_us_snapshot`(整檔 DELETE+INSERT),不經 `requested_start` 篩選 |
| `CallCountAtFetchTwLevelTest` | [永不炸] | 只斷言呼叫次數,不比較日期/序列內容 |

**`tests/test_eod_cache.py`**(3 個 class,全數 [永不炸]):純函數表格測試
(`_tw_cache_fresh`/`_tw_eod_hour`)全部顯式傳入 datetime,無 `datetime.now()`
依賴;整合測試用「相對年齡」(`timedelta(minutes/days=N)`)或 `_now_tpe` 完全
釘死時鐘,且僅有的 `fetch_tw` 觸發都是「快取命中直接短路」或「單次全量抓取」,
不落入窗篩選风险。

**`tests/test_twse_fallback.py`**(14 個 class,詳列於下):

| Class | 判定 | 理由 |
|---|---|---|
| `TwOfficialNumParsingPureFunctionTest`/`RocDateToIsoPureFunctionTest`/`ParseSnapshotPureFunctionTest` | [永不炸] | 純函數,無 `datetime.now()` |
| `MarketSnapshotMemoPureFunctionTest` | [永不炸] | `now` 全部顯式傳參,不讀真實時鐘 |
| `TwseHitSuccessTest`/`TpexFallbackWhenTwseMissTest`/`FinMindHealthyNoFallbackCallsTest` | [永不炸] | 單次呼叫、無 store 預先寫入,結果來自這次 raw_rows/官方快照,不受窗篩選 |
| `MemoizeTest`/`FullOutageMultiTickerCallCountTest` | [永不炸] | FinMind 恆失敗、`_now_tpe` 釘死,只斷言呼叫次數/memo,無序列比較 |
| `BothEndpointsFailOrMissTest` | [永不炸] | 過期快取斷言用相對 `timedelta(days=5)` |
| `HistoryStoreAssemblyAndClassifyWiringTest` | [已修/未來會炸] | 種子日期 `"2026-08-10/11"`/`"2026-07-01"` 屬「~2031 遠期」風險(見下），已改相對日期；`test_no_history_store_data_...` 不預寫 store,天生安全 |
| `ScaleGuardTest`(2 題) | [已修/未來會炸] | 同上,~2031 遠期風險,已改相對日期 |
| `DerivedFieldsAndG1FallbackTest`（8 題） | 混合 | 見下方獨立說明 |

`DerivedFieldsAndG1FallbackTest` 逐題:
- `test_dividend_yield_from_per_table_divided_by_100`、
  `test_f3_boundary_exactly_10_days_still_derives_per`、
  `test_f3_boundary_11_days_does_not_derive_per`、
  `test_trailing_eps_equals_price_over_per` → [未修/未來會炸],引爆日
  **約 2031-08-26**(store price 種子 `"2026-08-15"` + 5 年窗 1836 天）。
  未修理由:這 4 題的 PER 種子日期與 `d.price_date`(來自寫死 `roc_date="1150818"`
  轉換出的固定 `"2026-08-18"`)之間有精確的天數差語意(F3 10 天邊界測試尤其
  精確),若只把 store 日期改成相對「現在」、`roc_date` 不動,兩者關係會逐日
  漂移,**反而會把「~2031 遠期」風險換成「明天就漂移」的近期風險**;要正確修
  必須同步讓 `roc_date` 也動態產生(民國年字串轉換),牽涉面/風險與本工單
  「先讓 018 收斂」的急迫度不對稱,判斷**不在本工單修**,留給未來工單(建議
  一併給一個「動態產生民國日期字串」的共用 test helper）。
- `test_annual_dividend_ttm_cutoff_only_sums_within_365_days` → [已修/未來會炸],原引爆日
  **2027-01-01**(`cutoff=now-365天`、`ex>cutoff` 嚴格大於;now="2027-01-01" 當天
  `cutoff` 剛好等於除息日 `"2026-01-01"`,`>` 不成立即被排除,比 5 年窗更早
  翻紅,是本次掃雷發現最急迫的一顆——已改為相對「現在」動態生成解決)。
- `test_store_path_runs_back_adjust_tw_for_synthetic_split` → [已修/未來會炸],~2031 遠期
  風險(不涉 roc_date 協調，改法同 `ScaleGuardTest`）。
- `test_g1a_store_empty_blob_has_data_uses_blob_history_without_double_adjustment`、
  `test_g1b_existing_blob_content_byte_identical_after_fallback_success` → [永不炸]
  永不炸(走 blob 路徑,`_load_cache_raw` 直接沿用內容,完全不經 `requested_start`
  篩選)。

**`tests/test_month_revenue_guardrail.py`**(15 個 class,全數 [永不炸]):
`_compute_revenue_check`/`format_revenue_check_line`/`compute_zones` 皆為純函數
(TTM 終月來自輸入 rows 最後一筆,非 `datetime.now()`);`_monthrev_start_date`
唯一牽涉真實時鐘的測試(`test_2026_08_matches_real_finmind_call_used_in_report`)
顯式傳入固定 `_tpe(2026,8,20)`,不讀真實時鐘;月營收快取 TTL 用相對
age/`now` 參數;`FetchWiringCallCountTest` 系列只做單次 `fetch()`,固定日期
(`"2026-08-10"`)在此 ticket 未來 5 年內都在窗內、且斷言只看 `price`/呼叫次數,
不受影響。**不動月營收路徑**(未接 `history_store`,獨立 TTL 快取,結構性免疫）。

**其餘測試檔**(`test_providers_fallback.py`/`test_quality_warnings.py`/
`test_golden_valuation.py`/`test_classify.py`/`test_roi.py`/`test_back_adjust.py`/
`test_report_roi_display.py`)→ [永不炸]:grep 確認全部不 import/呼叫
`history_store`,也不呼叫 `fetch_tw`,結構性不落入 `_sync_and_assemble` 的窗篩選
路徑;唯一一處有 `annual_dividend`/`CashExDividendTradingDate` 字樣的
`test_providers_fallback.py::TwFinMindCallCountByMethodTest` 只斷言 `ok()`與
呼叫次數,不斷言 `annual_dividend`/`dividend_yield` 數值,TTM cutoff 漂移不影響
它。`test_golden_valuation.py` docstring 明文自陳「無 `datetime.now()`」為設計
原則,純確定性浮點運算。

### 紙上驗證(不真改系統時間)

- 用 Python 直接算 `window_start(years)` 公式(`now - (int(years*365.25)+10)天`）
  在 offset = −2..+3 天的實際字串值,證實:今天(2026-08-20)`years=5` 窗起點
  恰為 `"2021-08-10"`(與工單描述、`WindowSlicingTest` 原本寫死值精確重合),
  明天(2026-08-21)變成 `"2021-08-11"`——直接證明 `WindowSlicingTest` 修正前
  必定次日翻紅,不是臆測。
- 兩個原始紅 class:已在修正前實跑 `python -m unittest discover -s tests`,
  精確重現「就只有這 2 題紅、其餘 275 綠」,與工單描述的根因、失敗訊息
  (`AssertionError: Lists differ: [...('2021-08-09',...)] != [...]`，證實正是
  「首列被排除」）完全吻合。
- `DerivedFieldsAndG1FallbackTest` 4 題延後案例的引爆日用 Python 直接算
  `datetime(2026,8,15) + timedelta(days=int(5*365.25)+10+1)` = `2031-08-26`;
  `test_annual_dividend_ttm_cutoff` 原引爆日用 `datetime(2026,1,1)+timedelta(days=365)`
  = `2027-01-01` 得出(見上方判定表數字),非手動心算。

### Gate 結果

- `python -m py_compile tests/test_history_store.py tests/test_twse_fallback.py`
  → 無輸出,成功。
- `python -m unittest discover -s tests` → **`Ran 277 tests ... OK`**(修正前
  跑過一次確認「只有 2 題紅」,修正後再跑一次全綠,共執行 2 次，全程 0 API）。
- 未執行 `streamlit run app.py`(本工單未動任何 UI/`app.py`）。
- 未執行 `python monitor.py report --ticker 2330` 黃金值對照:本工單**只改
  `tests/` 兩個檔**,完全未碰 `aimonitor/`(估價/分類/ROI/拆股還原邏輯逐位不變），
  且該指令會打 FinMind API,與工單「0 API」要求衝突,故略過。

### 新增 API 呼叫評估

0。本工單全程未新增任何會打網路的呼叫;兩個修改檔案的所有測試維持既有的
`_http_get_json`/`urlopen` mock 紀律(`urlopen` 保險絲)不變。

### 剩餘風險

1. **`DerivedFieldsAndG1FallbackTest` 4 題「~2031-08-26」未修**(見上方判定表
   理由)——建議另開工單,同時提供一個「動態產生民國年日期字串」的共用 test
   helper(`_roc_date_of(datetime) -> str`),讓這 4 題與其他任何用得到
   `roc_date` 的測試都能一次修好,不需要逐題重新設計天數差關係。
2. `tests/test_history_store.py`/`tests/test_twse_fallback.py` 之外,本次判定表
   基於「grep 全文不含 `history_store`/`fetch_tw(`」排除其餘測試檔的風險——
   如果未來這些檔案新增呼叫 `fetch_tw`/`history_store` 的測試,判定表需要
   重新覆蓋(已在 `tests/test_history_store.py` 檔頭加防再犯慣例提醒,但那條
   慣例只會被讀到該檔的人看到,不是自動化檢查——若要更強的保證,未來可考慮
   寫一支「掃描 tests/ 找出寫死 `20\d\d-\d\d-\d\d` 且同檔案有 `history_store`/
   `_window_start` 字樣」的 lint script,此工單未做，屬於流程強化而非本工單
   SPEC 要求）。
3. 觀察到工作目錄內另有 018 尚未 commit 的既有變更
   (`aimonitor/providers.py`/`report.py`/`valuation.py`/`app.py`/
   `docs/api-budget.md`/`docs/tickets/018-monthly-revenue-guardrail.md`，及
   未追蹤的 `tests/test_month_revenue_guardrail.py`）——這些**不是本工單產生**
   (本工單只 `Read` 過 `tests/test_month_revenue_guardrail.py` 未曾寫入),原樣
   保留未動,供 orchestrator 判斷是否與 018 一併處理。
