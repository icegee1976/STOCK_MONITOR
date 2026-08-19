# 015 — 缺日偵測 + 資料品質警告(候選 B 之二;依賴 014)

狀態:**CLOSED**(2026-08-19,commit 見文末收斂紀錄)

## 收斂紀錄(orchestrator)

- Executor 交付 24 測試(162 全綠),mutation 自查(門檻 12→20)翻紅 6 題含兩條
  端對端接線,鑑別力有證據。SPEC 預釘的 blob 多鍵地雷被實測證實(TypeError 被
  既有 try/except 吞成「靜默 cache-miss、白打 API」——比炸更隱蔽),以
  `_STOCKDATA_FIELD_NAMES` 通用過濾根治,含對照組測試鎖住診斷前提。
- Orchestrator 仲裁:
  - **過濾順序親驗**:`_fetched_at` 於 L173 解析、L176 才過濾——時間戳不會被
    過濾掉,無靜默 cache-miss 風險。
  - **fetch_us_finnhub 邊界(採納,orchestrator 補一行)**:Finnhub 報價成功 +
    best-effort 歷史路徑同樣附掛偵測——雲端帶金鑰的使用者主要走這條,恰是最需要
    警告的族群;第三個相同呼叫點、空序列自然回空清單。162 題重跑全綠。
  - **免派獨立 reviewer(裁決)**:風險級別低於 013/014(不碰新鮮度/額度語意);
    唯一動到快取路徑的過濾行經 orchestrator 逐行驗證,且有 4 題 roundtrip(含
    對照組)鎖住;純函數 13 題表格 + mutation 證據;stale-rescue 帶舊警告的語意
    (舊資料配舊警告)已有測試且合理。
  - 黃金值:免真實連線——diff 零觸碰估價三模組,12 題黃金值測試在 162 綠燈內
    持續通過,接線點皆在數值算完之後(純附加),證據鏈完整。
- 收斂 gate:162/162 綠、py_compile 綠、0 API、diff 僅白名單。→ CLOSE。
  候選 B(014+015)至此完整:增量管線 + 耐久庫 + 缺日偵測。

## 背景
百分位/波動率/殖利率河流圖都假設歷史序列完整;FinMind 若有資料缺口目前**靜默偏差**
(013/014 reviewer 皆點名)。本單補「偵測 + 告知」,**不自動補抓**(避免對缺口
反覆重試的呼叫風暴;真補資料屬人工判斷)。

## SPEC(orchestrator)

### 偵測規則(純函數,providers.py 或 history_store.py)
`_series_quality_warnings(price_history, now=None) -> list[str]`:
1. **缺口**:相鄰兩筆日期差 > **12 個日曆天** → 警告(台股春節連假含前後週末最長約
   11 天,12 為安全門檻;美股假期更短,同一門檻通用)。訊息含缺口起訖與天數,
   並明說「百分位/波動率可能因此偏差」。
2. **尾端過舊**:序列最後一筆距 now > 10 個日曆天(抓取卻回報成功)→ 警告。
   now 用 `_now_tpe()`(可注入,測試釘假時鐘;US 用同一時鐘即可,誤差一天無妨)。
3. 序列 < 2 筆 → 不檢查(既有空資料路徑自有錯誤處理)。

### 接線與呈現
- `StockData` 新增 `quality_warnings: list = field(default_factory=list)`
  (blob 相容:舊 blob 無此鍵 → dataclass 預設;`StockData(**blob)` 需確認舊 blob
  多鍵/缺鍵行為,必要時在 `_load_cache_raw` 過濾未知鍵——**先驗證再改**)。
- fetch_tw / fetch_us 組裝完成後填入;分類/估價邏輯**不因警告改變任何數值**。
- 顯示(最小):`report.py::render_stock_card` 以黃色列出;`app.py` 個股分頁的
  warnings 迴圈附掛。免責與既有文案零改動。

### 測試(離線 0 API)
- 純函數表格:一般週末/9 天連假不警告;15 天洞警告(訊息含日期);尾端過舊警告
  (假時鐘);<2 筆不警告。
- blob 相容 roundtrip:新欄位存讀、舊 blob(無欄位)載入不炸。
- 顯示:render_stock_card 對含警告 stub 輸出黃色列(比照 test_report_roi_display 手法)。
- 既有全綠。mutation 自查:門檻改 20 → 15 天洞測試翻紅。

### 允許檔案
`aimonitor/providers.py`(偵測函數+接線+StockData 欄位)、`aimonitor/report.py`
(card 顯示 ≤5 行)、`app.py`(個股分頁 ≤4 行)、tests 新檔、api-budget §3 一句、本工單。

### 禁區
估價/分類/ROI 數值;自動補抓/重試;免責文案。

### API 呼叫評估
0 次(純本地檢查)。

## PLAN(executor 填)

1. **blob 相容驗證(先驗證,已用 python 實測)**:`StockData(**old_blob)`(缺鍵,例如舊快取
   沒有 `quality_warnings`)→ dataclass `default_factory` 正常補 `[]`,不炸 ✓。但
   `StockData(**{...,"unknown_key":...})`(多一個現在 dataclass 沒有的鍵)→
   `TypeError: StockData.__init__() got an unexpected keyword argument 'unknown_key'`,
   證實 SPEC 疑慮成立。**處置**:在 `_load_cache_raw` 依 `dataclasses.fields(StockData)`
   算出的已知欄位集合過濾 blob(只留已知鍵再建構),一次性防未來任何欄位增減造成的
   新舊版互讀 TypeError(不只這次的 `quality_warnings`,是通用向前相容防護)。
2. `StockData` 新增 `quality_warnings: list = field(default_factory=list)`；新增純函數
   `_series_quality_warnings(price_history, now=None)`(門檻常數
   `GAP_WARNING_DAYS=12`、`STALE_TAIL_WARNING_DAYS=10`,重用既有 `_days_between`),
   置於 providers.py「快取」段落之後、獨立分節,市場中立(TW/US 共用同一份邏輯與時鐘)。
3. 接線:`fetch_tw` 在 `_back_adjust_tw` 還原完成、`return d` 前填入(唯一「組裝完成」
   的終點);`fetch_us`(同時涵蓋 US 與 INTL,因 `fetch()` 的 INTL 分支就是呼叫
   `fetch_us`)在 `price_history` 組裝完成、`history_store.replace_us_snapshot` 呼叫之後
   填入。`fetch_us_finnhub` 在 Finnhub 報價成功、自行 best-effort 補歷史那條路徑
   ——SPEC 原文只列 `fetch_tw`/`fetch_us` 兩個函數名,不額外覆蓋這條路徑,於 REPORT
   記錄此邊界供 orchestrator/reviewer 判斷是否要追加。
4. 顯示:`report.py::render_stock_card` 在既有 `z["warnings"]` 迴圈後加一段黃色
   `data.quality_warnings` 迴圈(≤5 行);`app.py::analyze_stock` 的 `out["data"]` dict
   補 `quality_warnings` 鍵(接線,不算在個股分頁預算內);`_stock_tab_body` 在既有
   `z["warnings"]` 迴圈後加 `st.warning` 迴圈(個股分頁 ≤4 行)。
5. 測試新檔 `tests/test_quality_warnings.py`:純函數表格(9 天連假/12 天邊界皆不誤報、
   15 天洞誤報且訊息含日期、尾端過舊用假時鐘、<2 筆不檢查)、blob 相容 roundtrip
   (新存新讀、舊 blob 缺鍵不炸、模擬多鍵/未來欄位不炸且被過濾)、顯示測試
   (`render_stock_card` 黃色行,仿 `test_report_roi_display.py` 的 `HAS_RICH=False` +
   `redirect_stdout` 手法)。
6. mutation 自查:門檻 `GAP_WARNING_DAYS` 12→20,15 天洞測試應翻紅,跑一次記錄實際
   輸出進 REPORT。分類/估價/ROI 數值完全不動(警告是純附加資訊,不進入任何計算路徑)。

## REPORT(executor 填)

### blob 相容驗證結論與處置(先驗證,見 PLAN 第 1 點)
用 python 直接對 `StockData` 建構子實測(未動任何 code 前):
- 缺鍵(`StockData(**{"ticker":"2330","market":"TW","name":"台積電"})`,無
  `quality_warnings`)→ `dataclass` `default_factory` 正常補 `[]`,**不炸** ✓。
- 多一個現在 dataclass 沒有的鍵(`StockData(**{...,"unknown_key":...})`)→
  `TypeError: StockData.__init__() got an unexpected keyword argument 'unknown_key'`,
  **證實 SPEC 疑慮成立**。且原本的 `_load_cache_raw` 把整個讀檔+建構包在
  `try/except Exception: return None` 裡——若不修,這個 TypeError 不會顯眼地炸出來,
  而是被靜默吞成「當作快取不存在」,後果是**每次都誤判 cache miss、白白多打一次
  API**(比直接炸例外更隱蔽、更浪費額度)。
- **處置**:在 `StockData` 定義後新增 `_STOCKDATA_FIELD_NAMES = {f.name for f in
  fields(StockData)}`,`_load_cache_raw` 建構前先用它過濾 blob 只留已知鍵。這是
  通用的向前相容防護(不只保這次新增的 `quality_warnings`,未來任何欄位增減、或
  新舊版本交錯讀寫同一份 `.cache/` 檔案,都不會再因為多餘 kwarg 而 TypeError /
  靜默判假 cache-miss)。已補測試鎖住:`tests/test_quality_warnings.py` 的
  `BlobCompatibilityRoundtripTest`(含一則對照組
  `test_raw_stockdata_construction_rejects_unknown_kwarg_without_filter`,直接鎖住
  「dataclass 多鍵真的會 TypeError」這個診斷前提本身,不是憑空臆測)。

### DIFF 摘要
- `aimonitor/providers.py`(核心邏輯,無明文行數上限):
  - import 新增 `fields`(dataclasses)。
  - `StockData` 新增 `quality_warnings: list = field(default_factory=list)`。
  - 新增 `_STOCKDATA_FIELD_NAMES` 常數 + `_load_cache_raw` 內一行過濾未知鍵。
  - 新增獨立分節:`GAP_WARNING_DAYS=12`、`STALE_TAIL_WARNING_DAYS=10` 兩常數 +
    純函數 `_series_quality_warnings(price_history, now=None)`(市場中立,
    重用既有 `_days_between`;排序後掃相鄰缺口、檢查尾端距 `now` 是否過舊)。
  - `fetch_tw`:`return d` 前一行填入 `d.quality_warnings = _series_quality_warnings(d.price_history)`
    (`_back_adjust_tw` 還原完成之後,序列已是這次呼叫最終形狀)。
  - `fetch_us`:`price_history` 組裝完成、`history_store.replace_us_snapshot` 呼叫
    之後一行填入(涵蓋 US 與 INTL,因 `fetch()` 的 INTL 分支即呼叫 `fetch_us`)。
  - **範圍邊界(誠實揭露)**:`fetch_us_finnhub` 在 Finnhub 報價成功、自行
    best-effort 補歷史那條路徑(不經過 `fetch_us`)**沒有**接線
    `quality_warnings`——SPEC 原文只點名 `fetch_tw`/`fetch_us` 兩個函數,故未擴大
    範圍;若 Finnhub 報價失敗則整個退回 `fetch_us`(已覆蓋)。留給
    orchestrator/reviewer 判斷是否需要追加,未自行擴權。
- `aimonitor/report.py::render_stock_card`:既有 `z["warnings"]` 迴圈後新增 2 行
  (`for w in data.quality_warnings: _p(f"  [yellow]⚠ 資料品質:{w}[/yellow]")`)。
  **≤5 行預算內(實際 +2)**。
- `app.py`:
  - `analyze_stock()` 的 `out["data"]` dict 補 `quality_warnings` 鍵(+1 行,接線,
    非「個股分頁」本體)。
  - `_stock_tab_body()` 既有 `z["warnings"]` 迴圈後新增 2 行
    (`for w in d.get("quality_warnings", []): st.warning(f"資料品質:{w}")`)。
    **個股分頁 ≤4 行預算內(實際 +2)**。
- `docs/api-budget.md` §3 尾端追加一段(4 行)記錄 015:純本地檢查、0 新增 API、
  blob 向前相容處置。
- 新檔 `tests/test_quality_warnings.py`(24 個測試,見下)。
- 本工單檔案:填 PLAN + 本 REPORT。

**未動**:`watchlist.yaml`/`config.yaml`/校正產物/`valuation.py`/`classify.py`/`roi.py`
——分類/估價/ROI 計算路徑完全沒有觸碰,`quality_warnings` 全程只是附加資訊。

### 測試(tests/test_quality_warnings.py,24 個,全數離線 0 API)
1. `SeriesQualityWarningsPureFunctionTest`(13 個):<2 筆不檢查、一般週末不誤報、
   9 天連假不誤報、12 天邊界不誤報(SPEC `>`不是`>=`)、13 天剛過門檻警告、15 天洞
   警告且訊息含兩個日期/天數/「百分位」「波動率」「偏差」字樣、且不含任何買賣字眼
   (語氣中性)、未排序輸入仍正確偵測、尾端 10 天邊界不誤報、11 天剛過門檻警告、
   19 天假時鐘警告、`now=None` 時真的走 `_now_tpe()`(patch 驗證)、多個缺口各自
   產生一則警告。
2. `BlobCompatibilityRoundtripTest`(4 個):新欄位存讀 roundtrip 值不失真、舊 blob
   缺 `quality_warnings` 鍵不炸且補 `[]`、blob 多一個未來/未知鍵不炸且被過濾(其餘
   欄位正常讀回)、對照組直接鎖住「dataclass 建構子多鍵真的 TypeError」這個前提。
3. `FetchWiringQualityWarningsTest`(3 個,mock 網路,紀律同
   `tests/test_providers_fallback.py`):`fetch_tw`/`fetch_us` 在價格序列有缺口時
   真的把 `quality_warnings` 填進最終回傳的 `StockData`;另一則驗證 stale-rescue
   路徑(全源失敗、退回過期快取)會原樣帶著舊資料當時算出的舊警告一起回來。
4. `RenderStockCardQualityWarningsContentTest`(3 個)+
   `RenderStockCardQualityWarningsYellowMarkupTest`(1 個):比照
   `tests/test_report_roi_display.py` 的 `HAS_RICH=False`+`redirect_stdout` 手法驗證
   文字內容(有警告才印、空清單不印多餘內容、多筆警告各自一行);另外用假 Console
   攔截 `.print()` 收到的原始字串,直接驗證確實帶 `[yellow]...[/yellow]` 標記
   (滿足 SPEC「以黃色列出」,不只是驗證文字內容)。

**跑過的指令與結果**:
```
python -m py_compile aimonitor/providers.py aimonitor/report.py app.py tests/test_quality_warnings.py
  → 全部無錯誤
python -m unittest tests.test_quality_warnings -v
  → Ran 24 tests ... OK
python -m unittest discover -s tests
  → Ran 162 tests ... OK  (= 既有 138 + 本次新增 24;數字與 orchestrator 任務訊息
    的「138+新增」預期一致)
```

### mutation 自查(GAP_WARNING_DAYS: 12 → 20,跑完立即改回 12)
```
python -m unittest tests.test_quality_warnings -v   # 突變後
  → FAILED (failures=6)
```
翻紅的 6 個(全部合理,顯示測試真的有鑑別力,不只是「跑過而已」):
- `test_fifteen_day_hole_warns_with_dates_and_neutral_wording`(SPEC 明文要求的案例)
- `test_gap_just_above_threshold_warns`(13 天,20 門檻下不再 >20)
- `test_multiple_gaps_each_produce_own_warning`(19/21 天缺口,20 門檻下只剩 21 天那筆)
- `test_unsorted_input_still_detects_gap_correctly`(15 天缺口)
- `test_fetch_tw_wires_quality_warnings_on_price_gap` / `test_fetch_us_wires_quality_warnings_on_price_gap`
  (端對端接線測試,19 天缺口在 20 門檻下也偵測不到,證明整條接線、不只純函數本身
  有被鎖住)
改回 12 後重跑 `python -m unittest discover -s tests` → 162 個全綠,確認復原乾淨、
沒有殘留突變痕跡(已用 `git diff -- aimonitor/providers.py` 核對常數行文字與改動前
逐字一致)。

### 黃金值比對
**依 orchestrator 本次任務訊息明文 gate「0 API(不跑 live)」,本輪未執行
`python monitor.py report --ticker 2330` 真實連線比對**(該指令會打 FinMind,
與本工單「純本地檢查、0 API」的 SPEC 呼叫評估一致,兩者都指向不應該為了這張工單
額外打 API)。改用等效的離線證據鏈:
1. 本次 diff **完全沒有觸碰** `aimonitor/valuation.py`(估價)、`aimonitor/classify.py`
   (五帶分類)、`aimonitor/roi.py`(ROI)——`git status --short` 可核對只有
   `providers.py`/`report.py`/`app.py`/`docs/api-budget.md`/新測試檔變動。
2. `tests/test_golden_valuation.py`(鎖台積電 anchor 135.147 / 便宜 2228.57 /
   大特 1731.23 精確值,對照 PDF ≈2,226/≈1,729)**原封不動**,且是本次
   `unittest discover` 162 個全綠裡的一部分,持續通過。
3. `quality_warnings` 的兩處接線都在 `fetch_tw`/`fetch_us` 已經算完
   `d.price`/`d.price_history`/`d.per`/`d.dividend_yield` 等所有數值**之後**才執行
   (`fetch_tw` 在 `return d` 前最後一行;`fetch_us` 在 `history_store.replace_us_snapshot`
   之後、`info`/基本面處理之前——只讀 `d.price_history` 不寫回任何會被估價使用的
   欄位),純附加、不可能回頭改變已算完的數值。
**殘留風險**:未跑真實 CLI 交叉驗收這件事本身留給 orchestrator 決定是否要再補一次
(成本:2 次 FinMind 呼叫,`report --ticker 2330` 為 pe_band 方法)。

### 新增 API 呼叫評估
**0 次**。`_series_quality_warnings` 是純函數(只讀已經在記憶體裡的
`price_history` 列表 + 傳入的 `now`),不做任何 I/O;兩處接線都掛在
`fetch_tw`/`fetch_us` 既有成功路徑的尾端,呼叫數與 `docs/api-budget.md` §1/§2 表格
完全不變(已在該文件 §3 尾端追加一句記錄)。快取 blob 多一個欄位但走既有
`_save_cache`/`_load_cache_raw` 機制,不影響新鮮度判斷(TW EOD-aware / US 固定
15 分 TTL 皆未動)。

### 剩餘風險
1. **未跑 `streamlit run app.py` 即時冒煙**:同樣依 orchestrator「0 API」gate 選擇
   不跑(即使本機 `.cache/` 有舊資料,但已超過新鮮度門檻,`streamlit run` 仍會先
   嘗試真實連線才會落到過期快取保命,無法在不打 API 的前提下做到「真的啟動」的
   端對端冒煙)。改以 `python -m py_compile app.py` 通過 + 改動點本身是既有
   `@st.fragment` 函數內部的機械性小改(1 個 dict 鍵 + 1 個 `for/st.warning` 迴圈,
   語法/資料流都與緊鄰的既有 `z.get("warnings", [])` 迴圈同構)佐證低風險。若
   orchestrator 認為需要,可另外安排一次含真實資料的手動冒煙。
2. **`fetch_us_finnhub` 的 Finnhub-成功-自行補歷史路徑未接線**(見上方 DIFF 摘要
   的範圍邊界說明)——目前該路徑下 `d.quality_warnings` 維持 `[]`
   (`default_factory` 預設值),不會顯示任何缺口/過舊告知。是否要追加屬 SPEC
   範圍決策,非本輪自行認定。
3. `_series_quality_warnings` 的尾端過舊判斷用 `_now_tpe()`(台北時鐘)同時服務
   TW/US/INTL——SPEC 已明言「US 用同一時鐘即可,誤差一天無妨」,此為既定接受的
   簡化,非本工單新增風險,僅在此重申以便 reviewer 知悉此為刻意設計而非疏漏。
