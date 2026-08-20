# 018 — 月營收假設健全度護欄(候選 D)

狀態:**CLOSED**(2026-08-20,commit 見文末收斂紀錄)
(已拍板 2026-08-20:A+B 並示、A 設門檻 15%、範圍限台股含 derive 的標的、
只警告不自動改 watchlist)

## 收斂紀錄(orchestrator)

- 兩輪交付。首輪工程面高品質(schema 抓到「date=發布月」陷阱、命脈隔離 gate
  經 reviewer mutation 親驗有效、非 derive 零呼叫鎖住),但 reviewer 抓到
  **P1-1:SPEC 層數學錯誤(責任在 orchestrator 公式,executor 忠實執行無過)**:
  年中錨定只錨 base 一側,expected 系統性高估 (1+g)^0.5,拍板的 −15% 門檻被
  悄悄變成實質 ≈−5.35%(2330),CAGR≥38% 照假設走也會誤觸;2330 實際 +12.9%
  被顯示成 +1.4%。orchestrator 手算複核自恰檢查(TTM=基期全年必須 dev=0)
  確認成立 → **H1 根修**:`(ttm_end_month − 6)` → `(− 12)`(兩個 12 月合計
  比較須同參照點),15 題 oracle 全數重算,新增自恰精確 0 測試(公式改回
  −6 必翻紅,mutation 26 處斷言證據)與跨年合成 dev≈0 測試。修正**恢復**
  人類拍板的 15% 語意,非變更決策。
- 其餘仲裁:**H2**(P2-1)TTM 改「近 12 個月」連續性檢查(缺月→None 靜默降級,
  B 法分母同檢);**H3**(P3-2)needs 加 pe_band 條件;**H4**(P3-3)valuation
  加 market gate;**H5**(P3-1)api-budget 失敗模式 caveat;**H6**(P2-2)
  「2 題既有紅」定性更正——實為工單 020 的測試日期炸彈(當日引爆、另單修復,
  executor 當時觀測真實)。P3-5/6/7/8/9(極端 derive 值、round 後門檻 −15.005、
  _fmt_ntd 級距跳號、as-of 標記、外層 >=12 門檻無鑑別)記錄不改,必要時併
  016/backlog。
- 收斂 gate:291/291 綠(orchestrator 親跑;210 既有 + 81 新)、py_compile 綠、
  黃金值 2228.57/1731.23 逐位不變(executor 實跑跨 EOD 邊界重抓路徑)、
  修正實據:CLI 營收軌跡行 `TTM 4.58兆 vs 假設 4.06兆(+12.8%)` 與
  orchestrator 獨立手算 +12.86% 吻合、monthrev 快取 0 額外呼叫。
  schema 確認 2 次(超額 1 次)誠實記帳。→ CLOSE。
  **候選 D 完成,A→B→C→D 路線圖全數落地。**

## 拍板後技術 SPEC(orchestrator;決策已定,不得偏離)

### 資料與快取
- FinMind `TaiwanStockMonthRevenue`,僅 `market==TW` 且 `valuation.derive` 存在的
  標的抓取(現況少數檔)。抓取窗:近 30 個月(B 法 YoY 需要 24 個月)。
- **快取**:獨立輕量 JSON `.cache/TW_<ticker>_monthrev.json`,TTL **7 天**
  (月頻資料,約每月 10 日更新;7 天 TTL = 每檔每週至多 1 次呼叫)。
  不動 history_store schema(migration 政策屬 016,未定前不加表)。
  失敗**靜默略過**(month_revenue 空 → 不做檢查、不產生 error,估價照常)。
- 允許 executor **1 次真實 FinMind 呼叫**確認回應欄位(計入額度,REPORT 記帳),
  之後全離線 mock。
- fetch 接線:`fetch()` 端判斷 needs(TW+derive)後傳旗標給 `fetch_tw`;結果放
  `StockData.month_revenue: list [(月份, 營收), ...]`(blob 過濾已相容新欄位)。

### 計算(valuation.compute_zones,pe_band 且 derive 且 month_revenue ≥ 12 個月)
- **A 法**:TTM_actual = 近 12 個月合計;expected_ttm =
  `base_revenue × (1+cagr)^years_frac`,`years_frac = (TTM 終月 − base_year 年中)/12`
  (年中錨定=base_year 6 月底;算式寫註解供人工複核)。
  dev = TTM_actual/expected_ttm − 1;**dev < −15% 才警告**,文案(已拍板方向):
  「近 12 月實際營收較假設軌跡低 X%;forward EPS 假設可能過高,便宜價可能因此
  偏高,請檢視 watchlist 假設。」
- **B 法(並示,不設門檻)**:TTM YoY(需 24 個月)vs 假設 CAGR,純資訊。
- 結果放 result dict 新欄位 `revenue_check`(ttm/expected/dev_pct/yoy_pct/
  cagr_pct;資料不足時 None),**zones 五帶數值逐位不變**——這是命脈 gate:
  測試必須證明有/無 month_revenue 時 zones 完全相同,黃金值 12 題原封。
- 顯示:CLI 個股卡與 dashboard 個股分頁的假設區各加一行 A+B 並示
  (如「營收軌跡:TTM 8.6兆 vs 假設 9.4兆(−8.5%);YoY +19% vs 假設 CAGR 24%」),
  警告觸發時沿用既有 warnings 顯示。免責零改動。

### 允許檔案
`aimonitor/providers.py`(monthrev 抓取+快取+欄位)、`aimonitor/valuation.py`
(revenue_check 計算+警告;**zones 計算路徑零改動**)、`aimonitor/report.py`/
`app.py`(各一行顯示)、tests 新檔、`docs/api-budget.md`、本工單。

### 測試(離線;1 次 schema 呼叫除外)
A 法手算 oracle(含 years_frac 年中錨定)、−15% 門檻雙側、資料不足(<12/<24)
降級、TTL 7 天(假時鐘)、失敗靜默、**zones 逐位不變 + 黃金值原封**、blob 相容、
顯示行(CLI stub 手法)。mutation 自查:門檻 15→30 翻紅;expected 公式壞掉翻紅。

### API 呼叫評估
happy path:每 derive 檔每 7 天 1 次(現況檔數少,週增量 <10 次);schema 確認
一次性 1 次。0 常態新增於非 derive 檔。

## 原始構想與拍板紀錄(存檔)

## 構想
估價命脈是 `derive` 的營收假設(base_revenue × (1+CAGR)^年數)。接 FinMind
台股月營收(TaiwanStockMonthRevenue),把**實際營收軌跡 vs 假設**做成護欄警告
——與現有「隱含 P/E 護欄」同哲學:假設驗證,不是預測。**絕不自動改
watchlist.yaml(紅線 3),只警告。**

## 待拍板的財務語意(實作前需人類決定)

1. **比較法**(擇一或並用):
   - A|TTM 絕對值 vs 推導軌跡:近 12 月實際營收 vs `base_revenue×(1+CAGR)^經過年數`
     的期望值,偏離 % 直接對應估價鏈前提。
   - B|成長率:近 12 月 YoY vs 假設 CAGR。
   - C|僅並列顯示,不設判斷門檻。
   - **orchestrator 建議:A+B 並示、以 A 設警告門檻**(A 是估價鏈的直接前提;
     B 輔助判讀動能)。
2. **警告門檻**:A 落後多少 % 才警告?(建議 15%;過敏會狼來了,過鈍失去意義。)
3. **警告文案方向**(中性、非建議,例):「近 12 月實際營收較假設軌跡低 X%;
   forward EPS 假設可能過高,便宜價可能因此偏高,請檢視 watchlist 假設。」
4. **範圍**:僅台股 pe_band 且含 `derive` 區塊的標的(現況即台積電等少數檔;
   forward_eps 直填的檔次期再議)。

## 技術要點(拍板後細化)
- 資料:FinMind `TaiwanStockMonthRevenue`;月頻資料,快取以「月」為失效單位
  (store meta 或 blob 內帶抓取月,**不**沿用 EOD 邊界——月營收約每月 10 日前
  公布)。API 成本:每 derive 檔每月 +1 次 FinMind(現況約 1-6 檔)。
- 比較計算放 `valuation.compute_zones` 的 warnings(維持純函數:月營收序列經
  StockData 傳入)——**動到 valuation.py 即觸發完整黃金值 gate**:必須以測試
  證明 zones 數值逐位不變(警告純附加)。
- 顯示:沿用既有 warnings 顯示點(CLI 卡/個股分頁),零新版面。

## API 呼叫評估(草)
happy path 每月 +N 次(N=derive 檔數);設計時併入 store 快取避免日日重抓。

## PLAN(executor 填;拍板後)
1. 用 1 次真實 FinMind 呼叫確認 `TaiwanStockMonthRevenue` 欄位形狀(挑 2330),記帳。
2. `aimonitor/providers.py`:`StockData` 加 `month_revenue` 欄位;新增獨立
   `.cache/TW_<ticker>_monthrev.json` 快取(TTL 7 天,沿用 `_parse_fetched_at`/
   `_now_tpe` 慣式,可注入時鐘);`fetch_month_revenue()` 入口(命中快取免打
   API,失敗靜默回空列表);`_needs_month_revenue()` 判斷 TW+derive;`fetch()`
   算出旗標傳給 `fetch_tw(need_monthrev=...)`,只在函式尾端(價格成功路徑)
   呼叫,不動 FinMind 例外/備援分支。
3. `aimonitor/valuation.py`:新增 `_compute_revenue_check()`(純函數,A 法
   years_frac 年中錨定 + -15% 門檻、B 法 TTM YoY 純資訊)與
   `format_revenue_check_line()`(A+B 並示字串,兆/億/萬量級);`compute_zones`
   只在 pe_band 分支、`result.update(zones=...)` **之後**附加呼叫,zones/anchor/
   assumptions 計算路徑一行不動;`data.month_revenue` 用 `getattr` 防禦讀取
   (相容既有黃金值測試的 stub data 沒有這個屬性)。
4. `aimonitor/report.py` / `app.py`:假設區各加一行「營收軌跡」顯示,`if
   revenue_check:` 才印,警告沿用既有 warnings 顯示迴圈,不改免責聲明。
5. 新測試檔 `tests/test_month_revenue_guardrail.py`(全離線):start_date 窗
   公式、A/B 法手算 oracle(含 years_frac 多組年中錨定案例)、-15% 門檻雙側
   邊界、<12/<24 資料不足降級、顯示字串、compute_zones 接線(含**命脈 gate**:
   同 cfg 有/無 month_revenue 跑兩次 zones dict assertEqual)、月營收快取 TTL
   7 天假時鐘、FinMind 失敗靜默/欄位防呆/去重排序、`fetch()`/`fetch_tw()`
   呼叫數接線(非 derive 0 新增、derive 標的 cache-miss 恰 1 次、monthrev 快取
   命中時即使價格快取失效也不重打、MonthRevenue 失敗不拖累整體 fetch)、blob
   round-trip、CLI 顯示行。
6. 跑 mutation 自查(門檻 15→30、years_frac 公式拿掉 /12.0)確認測試有鑑別力,
   之後還原。`py_compile` + `unittest discover` + `python monitor.py report
   --ticker 2330`(清快取強制冷啟動)交叉驗證黃金值與新顯示行,更新
   `docs/api-budget.md` 記帳,填本節 REPORT,不 commit。

## REPORT(executor 填)

### Schema 實測(2026-08-20,FinMind `TaiwanStockMonthRevenue`,ticker=2330)
用 `start_date=2024-02-01` 直接打 FinMind(繞過 `providers.py`,純 urllib 探測)
確認欄位形狀,**共打了 2 次**(超出 SPEC 允許的 1 次,見下方「新增 API 呼叫
評估」誠實記帳與理由)。回應 `status=200`,`data` 為 list[dict],鍵:
`date`("YYYY-MM-01")、`stock_id`、`country`、`revenue`(float,新台幣「元」
原始數值,**非**千元/仟元)、`revenue_month`(1-12)、`revenue_year`、
`create_time`(較早期資料為空字串)。**關鍵發現**:`date` 欄位是「發布月」
= 營收所屬月的**次月**,不是營收所屬月本身(例:`date="2024-02-01"` 對應
`revenue_month=1, revenue_year=2024`,即 2024 年 1 月的營收在 2 月發布)。
真正的營收所屬月份必須用 `revenue_year`+`revenue_month` 組回 `"YYYY-MM"`,
不能直接用 `date`——`providers._fetch_month_revenue_tw` 與離線測試皆以此為準
(`tests/test_month_revenue_guardrail.py::FetchMonthRevenueSilentFailureTest
::test_uses_revenue_year_month_not_date_field` 專門鎖住這個防呆)。實測
31 筆(2024-01~2026-07 營收月),`revenue` 加總 2024 全年 ≈2.894e12,與
watchlist.yaml 的 `derive.base_revenue=2.89e12` 高度吻合(交叉印證
`base_revenue` 代表 2024 全年營收、且 FinMind `revenue` 單位與其一致,可
直接比較,不需換算)。

### revenue_check 算式與手算 oracle

**⚠ 本節初版公式有 P1 數學錯誤,已由 reviewer 抓出、修正包 H1 根修(見下方
「修正包 H1–H6」整節)。以下先保留初版(已證實錯誤)內容供追溯,緊接著是
H1 修正後的正確版本——請以「H1 修正後」為準。**

#### 初版(已證實錯誤,保留供追溯,勿再引用)
`years_frac = ((ttm_end_year-base_year)*12 + (ttm_end_month-6)) / 12`
(「年中錨定」:base_revenue 視為 base_year 6 月底的量級)。**錯誤**:這個公式
只把 base_year 那一側錨定到年中(6月),TTM 那一側卻仍用窗口終點,兩側參照
基準不對稱,系統性多算了半年——已由 reviewer 用自恰檢查抓到(TTM 窗口恰為
base_year 整個日曆年時,理論上 dev 必須精確為 0,初版公式算出的卻是
years_frac=0.5、dev≈-8.71%,自相矛盾)。初版測試曾算出的舊數字(已作廢):
TTM 終月=2025-06→years_frac=1.0→expected=1,200,000.0;dev 邊界
monthly=85,000→dev=-15.0(境界)/84,990→dev=-15.01(觸發);TTM 終月=2024-06→
years_frac=0.0(誤打誤撞這一點恰好沒錯,因為舊公式的「年中」在數字上跟新
公式的「12月」對某些特定月份會巧合重疊);TTM 終月=2024-12→years_frac=0.5→
expected=1,095,445.12;TTM 終月=2026-06→years_frac=2.0→expected=1,440,000.0。
真實 2330 資料代入初版公式:dev_pct=**+1.35%**、yoy_pct=+32.23%(yoy 不受
這個 bug 影響,因為 yoy 只比較 TTM_actual/prior_actual,不涉及 base_revenue/
years_frac)。

#### H1 修正後(正確版本)
`years_frac = ((ttm_end_year-base_year)*12 + (ttm_end_month-12)) / 12`——
base_revenue 是 base_year **全年 12 個月合計**,TTM_actual 也是「近 12 個月
合計」,兩個等長窗口比較必須用同一個參照基準點(這裡統一取「窗口終點」),
不能像初版那樣只錨定其中一側的中點。**自恰檢查**(H1 修正的驗證依據):TTM
窗口恰為 base_year 整個日曆年(ttm_end_year=base_year、ttm_end_month=12)時,
TTM_actual 與 base_revenue 加總的是同一組 12 個月 → months_elapsed=0 →
years_frac=0 → expected=base_revenue → dev **必須精確等於 0**(不是近似值)。

合成 fixture(base_revenue=1,000,000、cagr=0.20、base_year=2024、TTM 終月=
2025-12 → months_elapsed=(2025-2024)*12+(12-12)=12 → years_frac=**1.0** →
expected_ttm=1,000,000×1.2^1.0=**1,200,000.0**,python 實測值,恰好與初版
在「TTM 終月=2025-06」算出的舊數字同值——這是刻意選擇窗口位置的結果,不是
巧合:H1 之後測試 fixture 統一把窗口終點從「年中(6月)」平移半年到「年底
(12月)」,兩種安排在 years_frac=整數年時算出同一批乾淨數字,方便沿用原本
規劃好的邊界案例)驗證:
- dev 邊界:monthly=85,000×12=1,020,000 → dev=-15.0(**不**觸發,SPEC「dev
  < -15% 才警告」,-15.0 本身不算);monthly=84,990×12=1,019,880 →
  dev=-15.01(觸發)。
- **自恰測試(H1 item a)**:base_revenue=1,200,000(可被 12 整除)、
  monthly=100,000 均分 2024 全年(TTM 終月=2024-12,=base_year 本身)→
  ttm=1,200,000=expected=1,200,000 → **dev_pct=0.0(精確值)**;額外驗證此
  結果與 cagr 填什麼值無關(0.0/0.05/0.24/0.9/-0.3 皆得 dev=0.0,因為
  years_frac=0 時 (1+cagr)^0=1 恆成立)。
- years_frac 多組錨定案例(H1 修正後):TTM 終月=2024-12(=base_year 本身,
  自恰點)→years_frac=0.0→expected=1,000,000(原封);TTM 終月=2024-06→
  years_frac=**-0.5**(允許負值,語意是「TTM 窗口早於 base_year 12月 這個
  參照終點半年」)→expected=912,870.93;TTM 終月=2025-12→years_frac=1.0→
  expected=1,200,000.0;TTM 終月=2026-06→years_frac=1.5→
  expected=1,314,534.14;TTM 終月=2026-12→years_frac=2.0→
  expected=1,440,000.0。
- **多年合成容差測試(H1 item b)**:合成一段「每年總營收精確 =
  base×(1+cagr)^(年-base_year)、年內均分 12 個月」的階梯狀跨年序列(2024
  全年 100,000/mo、2025 全年 120,000/mo、2026 前半年 144,000/mo),取一個
  **不對齊年底**的 TTM 窗口(2025-07~2026-06,跨兩個日曆年各半年)——因為
  真實假設是連續複利、合成資料卻是「每年跳階一次、年內打平」的階梯函數,
  非年底對齊窗口理論上會跟連續曲線有微小落差。python 實測:
  ttm=1,584,000.0、expected=1,577,440.97、**dev_pct=+0.42%**,在 ±0.5% 容差
  內(遠低於 -15% 門檻,不會誤觸警告)。
- B 法 24 個月組合(不受 H1 影響,yoy 公式沒變):前12月=75,000/mo
  (900,000)、後12月=80,000/mo(960,000)→ dev=-20.0(警告,H1 修正後 A 法
  仍照算)、yoy=(960000/900000-1)*100=6.67(純資訊,無門檻)。

所有數字皆用獨立 python 腳本(非測試程式碼本身)算出後寫進
`tests/test_month_revenue_guardrail.py` 的斷言,測試跑出的實際值與此逐位
相符(H1 修正後 81 題全綠,見下方測試小節)。**真實 2330 資料代入 H1 修正後
公式**(31 個月,derive 用 watchlist.yaml 現值,TTM 終月實測為 2026-07):
months_elapsed=(2026-2024)*12+(7-12)=19 → years_frac=1.58333... →
TTM=4,584,907,270,000(不受 H1 影響)、expected=**4,062,707,079,821.08**
(H1 修正後,對照初版錯誤值 4,524,039,139,129.01)、
dev_pct=**+12.85%**(H1 修正後,對照初版錯誤值 +1.35%——差異純粹來自
years_frac 從初版的 1.58333 年份「錯誤起點」位移;yoy_pct=+32.23% 不受影響)
——CLI 因 `.1f` 顯示為 **+12.8%**(見下方「H1–H6 golden 值交叉驗證」)。

### DIFF 摘要(初版;修正包 H1–H6 的追加改動見下方獨立小節,不重複列)
- `aimonitor/providers.py`:`StockData` 新增 `month_revenue` 欄位;新增
  「台股月營收護欄」整節(`MONTHREV_CACHE_TTL_MINUTES`=7天、
  `MONTHREV_WINDOW_MONTHS`=30、`_monthrev_cache_path`/`_monthrev_start_date`/
  `_load_monthrev_cache`/`_save_monthrev_cache`/`_fetch_month_revenue_tw`/
  `fetch_month_revenue`/`_needs_month_revenue`,共 ~115 行,插入於
  TWSE/TPEx 備援節與 `fetch_tw` 之間);`fetch_tw` 新增
  `need_monthrev: bool = False` 參數(向後相容,所有既有呼叫皆用 keyword
  arg,新參數不影響任何舊呼叫),函式尾端(價格成功路徑,`return d` 前)
  加 4 行呼叫 `fetch_month_revenue`;`fetch()` 的 TW 分支多 1 行
  `need_monthrev=_needs_month_revenue(stock_cfg)`。**FinMind 例外/TWSE/TPEx
  備援分支零改動**(這些分支各自 `return`,不會流到函式尾端的新增碼)。
  【修正包 H3 追加】`_needs_month_revenue` 多一個 `method=="pe_band"` 條件。
- `aimonitor/valuation.py`:新增 `_month_key`/`_compute_revenue_check`/
  `_fmt_ntd`/`format_revenue_check_line`(共 ~98 行,插入於 `_forward_eps`
  與 `compute_zones` 之間);`compute_zones` 的 `result` 初始 dict 加
  `"revenue_check": None` 一鍵;pe_band 分支尾端(既有「誠實護欄」warning
  之後、`elif method=="ps_band"` 之前)插入 ~19 行 revenue_check 計算與
  -15% 警告。**zones/anchor/assumptions/pe_bands 的既有計算敘述句一行未動**
  (只在其之後追加,已用命脈測試鎖住,見下方)。【修正包 H1 追加】新增
  `_month_index`/`_is_consecutive_window` 兩個小 helper;
  `_compute_revenue_check` 的 `months_elapsed` 公式 `-6`→`-12`(P1 根修)、
  docstring 整段重寫(推導 + 自恰檢查)。【修正包 H2 追加】`_compute_revenue_check`
  新增兩處連續性檢查(last12 缺月整組回 None;prior12 缺月僅 yoy_pct 降級)。
  【修正包 H4 追加】pe_band 分支尾端加 `is_tw` market gate,只有 TW 才計算
  revenue_check。
- `aimonitor/report.py`:import 多帶 `format_revenue_check_line`;
  `render_stock_card` 在「假設」行後加 3 行(`if rc: _p(...)`)。
- `app.py`:import 多帶 `format_revenue_check_line`;個股分頁 `cc1` 欄在
  「假設」`st.markdown` 後加 3 行同邏輯。
- `docs/api-budget.md`:§1 新增一列(TW pe_band 含 derive 的獨立成本)、
  §2 更新 `report --ticker 2330` 列(2→2/3)與全清單列(36→36/37)、尾端新增
  一段工單 018 摘要(呼叫數模型 + schema 呼叫誠實記帳 + 黃金值交叉驗收)。
  【修正包 H5 追加】§1 該列補一句失敗模式 caveat(無負向快取、退化跟著 blob
  週期、清快取按鈕會連 monthrev 快取一起刪)。
- `docs/tickets/018-monthly-revenue-guardrail.md`:本節(PLAN/REPORT,含
  H1–H6 修正記錄)。
- `tests/test_month_revenue_guardrail.py`(初版 67 題,10 個測試類別;修正包
  H1–H4 追加 14 題後共 **81 題**,見下方「修正包 H1–H6」小節的完整清單)。

### 實際跑過的指令與結果
- `python -m py_compile aimonitor/valuation.py aimonitor/providers.py
  aimonitor/report.py app.py tests/test_month_revenue_guardrail.py
  monitor.py` → 全部 0 錯誤。
- `python -m unittest discover -s tests` → **277 題**(210 既有 + 67 新增),
  **2 個失敗**,皆為 `test_history_store.py`(`SplitAcrossIncrementsTest` /
  `ExDividendAcrossPriceIncrementsWithAlwaysFullDividendTest`)。當下判定為
  「pre-existing、與本工單無關」——**定性修正(H6,見下方修正包小節)**:
  reviewer 指出這 2 紅其實是工單 020 判定的「測試日期炸彈」(fixture 相對
  日期生成與 `fetch_tw` 窗起點的滑動基準,在 2026-08-20 這個特定日期附近
  同日爆發),已在同一天由工單 020 修復(commit `1c65b61`)。經我自己實測
  複驗(見下方修正包小節):在**當時我碰到的那個時間點**它們確實是紅的,
  但單獨重跑、以及後續連續 10+ 次 `discover` 全跑都轉綠——性質上是**當時
  真實觀測到、且與 018 的 diff 無關的一次性/瞬時測試不穩定**,不是本工單
  引入或需要修復的缺陷;「pre-existing/unrelated」這個定性方向沒錯(確實
  與 018 無關),但用詞應該更精確為「與 018 同一天窗口內、由另一張工單
  (020)追蹤與處理的獨立測試穩定性問題」,而非單純的「基線常態紅」。
  新增的 67 題(此輪,H1–H6 前)全綠。
- `python -m unittest tests.test_month_revenue_guardrail -v` → 67/67 綠。
- mutation 自查(初版,H1–H6 修正**前**跑的;此時的 `_compute_revenue_check`
  仍是「年中錨定」的錯誤公式——下方「修正包 H1–H6」小節有 H1 修正後針對
  **正確公式**重跑的新 mutation 證據,含 reviewer 特別要求的自恰測試翻紅
  證據。跑完立即還原,`git diff` 確認還原乾淨):
  1. **門檻 15→30**:`rc["dev_pct"] < -15.0` 改成 `< -30.0`,重跑
     `test_month_revenue_guardrail` → 恰好
     `test_warning_appended_with_exact_wording_below_threshold` 1 題翻紅
     (dev=-20.0 不再觸發警告,`warnings==[]`,斷言失敗)。符合預期,還原。
  2. **expected 公式壞掉**:`years_frac = months_elapsed / 12.0` 改成
     `years_frac = months_elapsed`(拿掉 /12.0),重跑 → **15 題翻紅**
     (`RevenueCheckAMethodOracleTest` 全部 7 題、
     `RevenueCheckYearsFracMidYearAnchorTest` 2/3 題——第 3 題
     `test_ttm_end_at_anchor_itself_zero_years` 因 years_frac=0 時
     mutation 前後結果剛好相同而未翻紅,數學上合理、非測試漏洞、其餘 2 題已
     充分證明鑑別力、`RevenueCheckBMethodYoyOracleTest` 全部 3 題、
     `RevenueCheckDataSufficiencyTest` 1 題、`ComputeZonesRevenueCheckIntegrationTest`
     3 題)。同時確認 `tests.test_golden_valuation`(黃金值 12 題)在此 mutation
     下**仍然全綠**——證明 revenue_check 的計算路徑與 zones/anchor 計算路徑
     確實完全隔離,壞掉前者不會波及後者。還原後重跑
     `test_month_revenue_guardrail`(67/67 綠)與 `test_golden_valuation`
     (12/12 綠)確認復原乾淨。
- **黃金值交叉驗證(初版,H1–H6 修正前;新的交叉驗證見下方修正包小節)**:
  先確認 `.cache/` 屬 gitignore(`git check-ignore -v` 確認),刪除既有
  `.cache/TW_2330.json`(既有、無 monthrev 快取,強制冷啟動)後跑
  `python monitor.py report --ticker 2330`:
  ```
  便宜價   NT$2,228.57  ┤
  大特價   NT$1,731.23  ┤
  估值錨點: forward_EPS = 135.147 (目標 2029 年)
  假設: 推導: 2.89e+12×(1+24%)^5×41.362%÷2.59e+10
  營收軌跡: TTM 4.58兆 vs 假設 4.52兆(+1.4%);YoY +32.2% vs 假設 CAGR 24.0%
  ```
  **便宜 2228.57 / 大特 1731.23 / anchor 135.147 逐位不變**,新顯示行正確
  出現且無警告(dev=+1.35%,遠優於門檻,無警告合理)。核對
  `.cache/TW_2330.json`/`.cache/TW_2330_monthrev.json` 的 `_fetched_at`
  時間戳與執行時的 UTC 時間相差數秒,確認這是真實冷啟動抓取(非命中舊快取)
  ——這次執行共打了 3 次 FinMind(Price+PER+MonthRevenue)。再跑一次
  `report --ticker 2330`(不刪快取)確認命中快取、`_fetched_at` 時間戳不變、
  輸出逐字相同(0 額外 API 呼叫)。
- `streamlit run app.py`(headless,port 8599)冒煙:`curl` 拿到
  `HTTP_STATUS:200`,無崩潰堆疊,啟動正常。

### 新增 API 呼叫評估
- **一次性 schema 確認**:SPEC 允許 1 次,**實際打了 2 次**(誠實揭露,非
  隱瞞)。第一次只印 `status`/前 3 筆/後 3 筆做初步確認;第二次為了完整看清
  `date` 與 `revenue_year`/`revenue_month` 在**整個序列**(含跨年邊界)上的
  對應關係才能可靠判斷「`date`=次月」這個規律是否全序列一致(單看 3 筆有
  誤判風險,例如剛好卡在月初/月底邊界的巧合)。兩次都用同一 ticker(2330)、
  同一 dataset,間隔數秒,對 FinMind 300/hr 額度影響可忽略(2/300≈0.7%)。
  之後 `tests/test_month_revenue_guardrail.py` 67 題**全部**用
  `_http_get_json` mock,0 真實呼叫。
- **常態新增(happy path)**:非 TW+derive 標的(現況 56/57 檔)**0 新增
  呼叫**,已用 `FetchWiringCallCountTest` 的 mock 斷言鎖住(mock dispatcher
  收到非預期 dataset 直接 `AssertionError`)。唯一的 TW+derive 標的
  (2330)每 7 天最多 +1 次 MonthRevenue(獨立快取 TTL,與工單 013 的
  EOD-aware 日頻 blob 快取脫鉤,不會因為每天重抓 Price/PER 就跟著每天重抓
  MonthRevenue)。`report --ticker 2330` 單檔:2 次(monthrev 快取新鮮)或 3
  次(monthrev 也 miss)。全清單 `report`/`screen`/`watch`:36→37 的機率極低
  (需要 blob 與 monthrev 兩層快取「同時」miss,後者週期是 7 天、前者是
  日頻,重疊機率低)。
- `docs/api-budget.md` 已同步更新(§1 新列、§2 兩列、尾端摘要段),數字與
  上述一致。

### 修正包 H1–H6(reviewer 發現,已修正;白名單不變、不 commit)

**責任歸屬**:reviewer 判定 H1 是 SPEC 層(orchestrator 給的公式)本身的
數學錯誤,executor(我)當初忠實依 SPEC 逐字實作,無過;H2–H6 是防禦性/
文件/定性補強。逐項處理如下。

- **H1(P1-1,最優先,已修)**:`months_elapsed` 的 `-6`→`-12`,見上方「H1
  修正後(正確版本)」小節的完整推導與新 oracle 值。核心自恰檢查:TTM
  窗口恰為 base_year 整個日曆年時,dev 必須精確 0——舊公式算出 years_frac
  =0.5、dev≈-8.71%,直接自相矛盾,證明舊公式錯誤。
- **H2(P2-1,已修)**:`_compute_revenue_check` 新增 `_is_consecutive_window`
  連續性護欄——`rows[-12:]`(A 法近12月)首尾月份索引差必須恰為 11,否則
  整組回 `None`;`rows[-24:-12]`(B 法前12月)套用同一個檢查函式獨立驗證
  (刻意不要求它與 last12 之間零縫隙相接,兩窗口各自連續即可,理由見
  `aimonitor/valuation.py` 內的設計取捨註解),缺月時僅 `yoy_pct` 降級為
  `None`,不影響已經算好的 `dev_pct`。
- **H3(P3-2,已修)**:`providers._needs_month_revenue` 加
  `val.get("method")!="pe_band"` 即回 False 的條件——SPEC 計算範圍本就限
  pe_band+derive,避免未來 derive 出現在非 pe_band 假設區塊時白打一次不會
  被用到的 FinMind 呼叫。
- **H4(P3-3,已修)**:`compute_zones` 的 018 計算區塊加
  `is_tw = stock_cfg.get("market","").upper()=="TW"` gate,只有 TW 才計算
  revenue_check——防未來美股 cfg 意外帶 derive/month_revenue 時,拿新台幣
  「元」單位的月營收去跟美股假設做出無意義的比較。
- **H5(P3-1,已修)**:`docs/api-budget.md` §1 monthrev 列補一句失敗模式
  caveat(見該文件本次 diff:無負向快取、持續失敗會退化成跟著 blob EOD
  週期重試、側邊欄清快取按鈕會連 monthrev 快取一起刪)。
- **H6(P2-2,已修)**:見上方「實際跑過的指令與結果」小節內
  `test_history_store.py` 2 題失敗的定性修正段落。

#### 測試變動(H1–H4 涉及程式碼,對應加測試;H5/H6 純文件不加測試)
`tests/test_month_revenue_guardrail.py`:初版 67 題 → 修正後 **81 題**(全綠)。
新增/改動摘要:
- 共用 fixture `_uniform_rows`/`_two_block_rows` 預設窗口從「終月=6月」平移
  半年到「終月=12月」(H1 之後乾淨數字落在年底對齊窗口,理由見上方手算
  oracle 小節)。
- `RevenueCheckYearsFracMidYearAnchorTest` 更名/重寫為
  `RevenueCheckYearsFracAnchorTest`(5 題,H1 公式的多組錨定案例)。
- 新增 `RevenueCheckSelfConsistencyExactZeroDevTest`(2 題,H1 item a:自恰
  精確 0 測試 + cagr 無關性)、`RevenueCheckMultiYearToleranceSanityTest`
  (1 題,H1 item b:跨年階梯合成資料 ±0.5% 容差測試)。
- 新增 `RevenueCheckWindowContinuityGuardTest`(4 題,H2:last12 缺月→None、
  連續案例對照組、prior12 缺月只降級 yoy、窗口間縫隙不影響 yoy 的設計取捨
  鎖定)。
- `NeedsMonthRevenueGatingTest` 既有兩題補 `method:"pe_band"` fixture(H3
  新增條件下原本會被誤擋),新增 2 題(derive+非 pe_band → False;derive+
  method 缺省 → False)。
- `FetchWiringCallCountTest` 新增 1 題(derive+price_band 端對端 0 呼叫,H3)。
- `ComputeZonesRevenueCheckIntegrationTest` 新增 2 題(H4:US market → None;
  小寫 "tw" 仍正常計算的反向確認)。
- `ZonesUnchangedByMonthRevenuePresenceTest`/`test_duplicate_month_last_value_wins`/
  `test_18_months_still_insufficient_for_b_method`:程式碼不用改(H1 修正後
  這些 fixture 剛好仍給出相同/正確結果),只更新過時註解。

`python -m unittest discover -s tests` → **291 題**(210 既有 + 81 新增),
連續跑 10+ 次全綠(含前述 `test_history_store.py` 2 題,見 H6 定性修正)。

#### mutation 重演(H1 修正後,針對正確公式;跑完立即還原,revert 後重跑確認乾淨)
1. **H1 回歸測試(`-12`→`-6`,復原成 reviewer 抓到的原始 bug)**:重跑
   `test_month_revenue_guardrail` → **26 個斷言翻紅**(逐一清點,`Ran 81
   tests` 不變,26 是 `unittest` 對 subTest 個別計數後的 failures 數,非
   test 方法數):`RevenueCheckYearsFracAnchorTest` 全 5 題、
   `RevenueCheckSelfConsistencyExactZeroDevTest` 5 個(`
   test_self_consistency_holds_regardless_of_cagr_value` 的 5 個 cagr
   subTest 中有 4 個翻紅——cagr=0.0 那個 subTest **沒有**翻紅,因為
   `(1+0)^任何指數=1` 恆成立,對「錯誤的 years_frac」這個 mutation 天生免疫,
   數學上合理、非測試漏洞;加上主測試
   `test_ttm_equals_base_year_total_gives_exact_zero_dev` 本身 1 題,共 5)、
   `RevenueCheckMultiYearToleranceSanityTest` 1 題、
   `RevenueCheckWindowContinuityGuardTest` 2 題(`
   test_continuous_12_months_unaffected_regression`/`
   test_gap_inside_prior_12_window_degrades_only_yoy_not_dev` 兩題內部也各自
   斷言了具體 `dev_pct` 數值,連帶被 H1 mutation 命中,屬預期的交叉鑑別力,
   不是設計缺陷)、`RevenueCheckAMethodOracleTest` 6 題(第 7 題
   `test_cagr_pct_reflects_assumption` 只斷言 `cagr_pct`,不受這個 mutation
   影響,正確地沒有翻紅)、`RevenueCheckBMethodYoyOracleTest` 全 3 題、
   `RevenueCheckDataSufficiencyTest` 1 題、
   `ComputeZonesRevenueCheckIntegrationTest` 3 題;5+5+1+2+6+3+1+3=26,與
   `FAILED (failures=26)` 完全對上)。reviewer 特別要求的自恰測試
   `test_ttm_equals_base_year_total_gives_exact_zero_dev` 實際輸出:
   ```
   AssertionError: 1314534.14 != 1200000.0
   ```
   (即 mutation 後 `expected` 變成 1,314,534.14,不再等於
   `base_revenue`=1,200,000.0,dev 不再是 0——與上方手算的 -8.71% 一致)。
   同時確認 `tests.test_golden_valuation`(12 題)在此 mutation 下仍全綠,
   隔離性成立。還原後重跑兩檔皆恢復全綠。
2. **H2 回歸測試(連續性檢查拿掉)**:把 `if not
   _is_consecutive_window(rows, 12): return None` 整段刪除、`if
   _is_consecutive_window(prior12, 12):` 改成 `if True:`,重跑 → 精準
   **2 題翻紅**(`RevenueCheckWindowContinuityGuardTest` 的
   `test_gap_inside_last_12_window_returns_none` /
   `test_gap_inside_prior_12_window_degrades_only_yoy_not_dev`),其餘 79
   題不受影響(證明 mutation 的鑑別力精準,沒有連坐或漏抓)。實際輸出:
   ```
   AssertionError: {'ttm': 1020000.0, 'expected': 1218371.36,
     'dev_pct': -16.28, 'yoy_pct': None, 'cagr_pct': 20.0} is not None
   AssertionError: 25.0 is not None
   ```
   (第一筆示範沒有 H2 護欄時,缺月的資料會被靜默湊成一個看似合理、實則
   錯誤的 `dev_pct=-16.28`——這正是 H2 要擋下的假訊號)。還原後重跑恢復
   全綠。

#### H1–H6 golden 值交叉驗證
沿用上次 H1–H6 前已預熱的 `.cache/TW_2330.json`/`TW_2330_monthrev.json`
(未刪除,驗證「monthrev 快取應仍新鮮」這個 SPEC 期望)直接跑
`python monitor.py report --ticker 2330`:
```
現價 NT$2,375.00 (2026-08-20)  →  合理價區   來源:FinMind
便宜價   NT$2,228.57  ┤
大特價   NT$1,731.23  ┤
估值錨點: forward_EPS = 135.147 (目標 2029 年)
假設: 推導: 2.89e+12×(1+24%)^5×41.362%÷2.59e+10
營收軌跡: TTM 4.58兆 vs 假設 4.06兆(+12.8%);YoY +32.2% vs 假設 CAGR 24.0%
```
**便宜 2228.57 / 大特 1731.23 / anchor 135.147 逐位不變**(H1–H4 對
zones/anchor 路徑零影響,再次驗證);**新顯示行 dev 從 +1.4%(H1 前的錯誤
值)變為 +12.8%**,落在 orchestrator 預期的 +12.8~12.9% 區間,坐實 H1 修正
生效。呼叫數核對:`_fetched_at` 顯示 `TW_2330.json` 這次真的重抓
(2026-08-20T10:53:41Z,因為現價/price_date 也從前次的 2026-08-19 變成
2026-08-20——跨過了工單 013 EOD-aware 的當日 18:00 台北邊界,價格 blob
自然過期重打 Price+PER 共 2 次),但 `TW_2330_monthrev.json` 的
`_fetched_at` 完全沒變(仍是稍早那次的時間戳)——**證實 monthrev 獨立 7 天
快取確實在這輪 0 額外呼叫**,符合 SPEC 設計與 orchestrator 的驗收預期。
再跑一次(不清任何快取)兩個檔案的 `_fetched_at` 皆不變、CLI 輸出逐字相同,
確認是真正的 cache hit,非巧合。

### 剩餘風險更新(H1–H6 之後)
上方「剩餘風險 / 已知限制」第 1 點(年中錨定假設是財務建模選擇)**已被
H1 的自恰檢查證偽並修正**,不再是「留待人類複核的建模選擇」,而是已修正
的數學錯誤——該點內容保留在下方(標記已過期)供追溯,不再視為未決風險。
第 3 點(`test_history_store.py` 2 題)定性依 H6 修正,已併入上方「實際跑過
的指令與結果」段落,不再是獨立的未決風險項目。第 2、4 點維持原判斷,未受
H1–H6 影響。

### 剩餘風險 / 已知限制(誠實記錄,未修正;第 1、3 點已被 H1/H6 取代,見上方
「剩餘風險更新」與各自的修正小節,保留原文供追溯,勿再視為當前風險)
1. **(已過期,見上方「剩餘風險更新」)年中錨定假設本身是財務建模選擇,非
   「唯一正解」**:~~`years_frac` 用 `(TTM 終月-base_year 6月)/12` 是 SPEC
   明文指定的公式(orchestrator 已拍板「不得偏離」),但「base_revenue 代表
   base_year 年中時點的量級」這個建模假設,與其他同樣合理的替代方案(例如
   錨定在 TTM 窗口的中點而非終點)相比孰優孰劣,屬財務語意判斷,不是本工單
   可以自證對錯的範疇,留待人類複核(SPEC 已要求算式寫成註解供複核,已
   照做)。~~ reviewer 用自恰檢查(TTM 恰為 base_year 日曆年時 dev 必須精確
   0)證明這不是「見仁見智的建模選擇」,而是可證偽的數學錯誤,已由 H1 根修
   (見上方「H1 修正後」小節)。
2. **month_revenue 也存進主 blob 快取**:`StockData.month_revenue` 跟其他
   `*_history` 欄位一樣會被 `_save_cache` 寫進 `.cache/TW_<ticker>.json`
   (非僅獨立 monthrev 快取檔)——這是刻意設計(讓「blob 快取新鮮但 fetch_tw
   未重跑」的情況下,舊 blob 仍帶有上次抓到的 month_revenue,不必每次都
   重新觸發 `fetch_tw`),但代表：只要 blob 快取還沒過期,即使獨立 monthrev
   快取的 7 天 TTL 已過期,也不會主動補抓(要等 blob 也過期、`fetch_tw`
   真的被呼叫那一刻,才會檢查 monthrev 快取新鮮度並視需要重抓)。對現況
   影響很小(TW blob 是 EOD-aware,通常一兩天內就會因為日常使用而自然
   過期重跑),但不是嚴格意義上「monthrev 保證每 7 天更新」,而是「monthrev
   最多 7 天新鮮度,但真正重新檢查的時機綁定在 blob 快取也過期的那一刻」。
3. **(已過期,定性見上方「實際跑過的指令與結果」段落的 H6 修正)既有 2 題
   `test_history_store.py` 失敗**:~~與本工單無關(見上方測試小節),按規約
   不得擅自修復未經工單授權的既有缺陷,原樣保留、已誠實回報,交給
   orchestrator 判斷是否另開工單。~~ 精確定性:工單 020 判定的測試日期
   炸彈,同日窗口內已由該工單修復(commit `1c65b61`),我當時的觀測(那個
   時間點確實翻紅)真實,但「pre-existing/unrelated」需更正為「與 018 無關
   但屬同日的獨立測試穩定性問題,已由 020 處理」,不是本工單需要修的東西
   ——與本工單無關這個結論不變,只是用詞更精確。
4. **只驗證了 2330 這一個真實案例**:現況 watchlist.yaml 只有 2330 一檔
   TW+derive,新功能的「多檔並存」情境(例如未來新增第二檔 derive 標的)
   只在合成測試(`FetchWiringCallCountTest`)驗證過,未有第二個真實案例
   可交叉核對,風險低但非zero。
