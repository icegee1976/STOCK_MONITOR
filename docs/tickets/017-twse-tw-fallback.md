# 017 — 台股第二資料源備援:TWSE/TPEx 官方 OpenAPI(候選 C)

狀態:**CLOSED**(2026-08-19,commit 見文末收斂紀錄)

## 收斂紀錄(orchestrator)

- 兩輪交付。首輪功能本體紮實(真實 schema 驗證抓到 TPEx " ---" 佔位符實戰邊角;
  FinMind 成功路徑 byte-untouched;192 綠),但 reviewer 抓到**三條真 P1**,全在
  「備援成功之後」的副作用:
  - **P1-1**:備援結果寫 blob 會覆蓋上一份完整 FinMind 快取且被 EOD 新鮮度釘住
    22~71 小時(0056 實測:yield_band 從完整分析變 ValuationError 且救不回)。
    → **G1 根修**:組裝歷史 store 優先、空時退回舊 blob(注意 blob 已還原不可
    二次 _back_adjust_tw,store 原始值才要——有測試鎖);備援結果一律**不寫 blob**
    (source 含「(備援)」跳過 _save_cache,約定與代價註解在案)。備援自此嚴格
    ≥ stale-rescue,FinMind 恢復即刻回正常路徑。
  - **P1-2**:官方價與 store 歷史尾端尺度脫鉤 → 假大特價+假 100% 觸及+假買進
    桌面通知(紅線 2)。→ **G2**:比值落拆股門檻即棄歷史+明確警告,寧可 auto 帶
    誠實 ValuationError。
  - **P1-3**:全網路故障時每檔重打兩端點,最壞 ~64 分鐘掛死。→ **G3**:空/非 dict
    視為失敗不記牢、失敗負向 memo 15 分、timeout 10s——整輪首檔付探測成本即止。
  - **P2-2**:組裝派生零測試覆蓋(reviewer 兩批 mutation 全綠實證)→ **G4** 補齊
    (/100、F3 雙側、TTM cutoff、備援還原、blob 退援、不寫 blob、尺度護欄)。
  - G5 文件數字修正;G6 逸出例外防禦;G7 `_reset_snapshot_memos()` 測試隔離;
    G8 解析補強(isfinite、ASCII-only 民國日期);P2-4 memo 不吃 config 覆寫
    記為 docstring 已知限制。
- 誠實揭露:schema 確認實際打了 7 次(SPEC 允許 2)——免金鑰公開端點、零
  FinMind/Finnhub 額度影響,屬流程落差非額度風險,如實記帳。
- 收斂 gate:210/210 綠(162 既有原封+48)、py_compile 綠、三 mutation 翻紅有據、
  黃金值經真實 cache-miss 交叉不變、全 diff 僅刪一行(無條件存檔→條件式)、
  orchestrator 親驗 skip 條件與單行刪除宣稱。→ CLOSE。
- 殘留(不擋):G1a 全有全無不比新舊、G2 只比尾端單點、負向 memo 15 分冷卻的
  UX、雲端 egress 可達性未實測——均記於 REPORT,必要時併 016/019 追蹤。

## 背景
降級鏈不對稱:美股有 Finnhub→yfinance→過期快取三層,台股只有 FinMind 單源
(掛了只剩過期快取)。TWSE(上市)與 TPEx(上櫃)官方 OpenAPI 免費、免金鑰、
**一次呼叫回全市場當日行情**——當 FinMind 失敗時以 2 次呼叫覆蓋全部台股標的。

## SPEC(orchestrator)

### 降級順序(僅在 FinMind price 抓取失敗時觸發)
```
fetch_tw: FinMind 失敗(402/斷線)
  → TWSE STOCK_DAY_ALL(上市)/ TPEx 對應日行情 endpoint(上櫃)找現價
      找到 → price/price_date 用官方 EOD;price_history/per_history/div_history
             盡量從 014 歷史庫組裝(有就有,沒有就空——分類對明確帶仍可用,
             auto 類自然走既有 ValuationError 路徑);source 標「TWSE(備援)」
             /「TPEx(備援)」;quality_warnings 照 015 附掛;成功 → _save_cache
             (blob/EOD 新鮮度自然接手)
      找不到(兩端點都無此代號或端點失敗)→ 維持現狀:回傳 error → stale-rescue
```
- 402 等 FinMind 錯誤訊息**保留**(備援成功時可附註「已改用 TWSE 備援」風格訊息
  或 source 標示即可;失敗時原訊息照舊)。
- **全市場回應 memoize**:同一輪(多 ticker)只打 TWSE/TPEx 各一次——以
  process 內 memo + 013 的 EOD 邊界判斷失效(跨邊界才重打)。不落地新檔案
  (blob/store 沿用既有層)。

### 端點與資料形狀(允許 2 次真實呼叫確認 schema)
- TWSE:`https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`(免金鑰)。
- TPEx:`https://www.tpex.org.tw/openapi/v1/` 下對應主板日行情 endpoint。
- Executor **允許各打 1 次真實呼叫**抓實際欄位名(免費官方端點,非 FinMind/Finnhub
  額度紅線),把實際回應形狀(欄位名、日期格式、數值是否含逗號字串)記進 REPORT,
  mock 以實測形狀為準;之後測試全離線。注意民國/西元日期格式轉換。

### 允許檔案
`aimonitor/providers.py`(fallback 函數 + fetch_tw 接線;既有 FinMind 路徑
成功時零行為變更)、`tests/test_twse_fallback.py`(新)、`docs/api-budget.md`
(§1/§2 補備援成本、☁ 相關敘述如需)、`README.md` 僅資料源一句(若動,§4/§5
免責零改動)、本工單。

### 測試(離線;mock 紀律沿用 005)
1. FinMind 失敗 + TWSE 有該代號 → 備援成功、source 標示、price 正確(含逗號
   字串/民國日期的解析案例)、blob 有寫入。
2. 上櫃代號:TWSE 無 → TPEx 有 → 成功。
3. memoize:同輪第二檔 → TWSE/TPEx 呼叫數不增(URL 捕捉);跨 EOD 邊界(釘假
   時鐘)→ 重打。
4. 兩端點皆無/皆失敗 → 行為與現狀逐位相同(error → stale-rescue,回歸鎖)。
5. FinMind 正常時 → TWSE/TPEx 呼叫數 == 0(不偷打,回歸鎖)。
6. 歷史庫有資料時備援結果含組裝歷史;無資料時 price_history 空但分類明確帶可用
   (驗 compute_zones 對固定帶 stub 不炸——只驗接線,不動 valuation)。
7. mutation 自查:memoize 拿掉 → 呼叫數測試翻紅;fallback 順序反轉(先打 TWSE)
   → 測試 5 翻紅。既有 162 題原封全綠。

### 禁區
FinMind 成功路徑行為;blob/EOD/rescue/store 語意;估價/分類/ROI;免責文案。

### API 呼叫評估
常態 0(FinMind 正常時零觸發);FinMind 故障期間每 EOD 邊界 2 次(TWSE+TPEx,
免金鑰、覆蓋全清單)。Schema 確認一次性 2 次真實呼叫。

## PLAN(executor 填)

1. 一次性真實呼叫確認 schema(各 1 次,免金鑰官方端點):TWSE
   `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`、TPEx
   `www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`(SPEC 假設路徑第一次猜就中,
   不需要試錯其他候選)。記錄實際欄位/日期/數值格式進 REPORT,之後測試全離線。
2. `aimonitor/providers.py` 在 `_sync_and_assemble` 與 `fetch_tw` 之間新增一個章節:
   - 純函數 `_tw_official_num`(官方數值字串防呆:去逗號/空白,佔位符如 " ---" → None)、
     `_roc_date_to_iso`(民國年日期 → 西元 "YYYY-MM-DD")。
   - `_parse_twse_snapshot(raw)` / `_parse_tpex_snapshot(raw)`:list[dict] → `{ticker: (price, date)}`,
     單列解析失敗不拖垮整批。
   - 模組層 memo `_TWSE_SNAPSHOT_MEMO` / `_TPEX_SNAPSHOT_MEMO`(dict,`{"data":..., "fetched_at":...}`)
     + `_market_snapshot(memo, url, parse_fn, now=None)` 的 get-or-fetch 骨架,新鮮度**重用**
     `_tw_cache_fresh`(工單 013)判斷是否跨過 EOD 邊界,不發明新規則;失敗保留舊 memo。
   - `_twse_fallback_quote(ticker, now=None)` / `_tpex_fallback_quote(ticker, now=None)`:
     回傳 `(price, date) | None`(SPEC 點名的函數;拆成兩個對稱的單一端點函數,而非一個函數
     內部涵蓋兩端點,理由見 REPORT)。
   - `_assemble_tw_fallback(ticker, name, price, price_date, source_label, requested_start)`:
     組出全新 `StockData`——`price`/`price_date` 固定用官方 EOD(不被下面組裝出的歷史序列
     覆蓋);`price_history`/`div_history`/`per_history` 純讀 `history_store`(不呼叫 FinMind,
     不走 `_sync_and_assemble` 增量骨架);沿用工單 014 F3 的 10 天 PER 新鮮度護欄;
     `quality_warnings` 照 015 附掛 + 加一行中性語氣的「已切換備援」說明。
3. `fetch_tw` 的 `except Exception as e:` 分支尾端接上:先設 `d.error`(逐位不變)→ 試
   `_twse_fallback_quote` → 沒有再試 `_tpex_fallback_quote` → 找到就 `return
   _assemble_tw_fallback(...)`(包一層 try/except 防止組裝本身意外拋例外拖垮整個 fetch_tw)
   → 都沒有就 `return d`(現狀路徑逐位不變)。FinMind 成功路徑(`try` 區塊本體)不動一行。
4. 新測試檔 `tests/test_twse_fallback.py`:mock 紀律沿用工單 005(CACHE_DIR 隔離、urlopen
   保險絲、`_http_get_json` 逐條 URL 路由、環境變數快照),外加模組層 memo 的
   `patch.object` 隔離(比照 CACHE_DIR 手法)。涵蓋 SPEC 七組測試 + 純函數表格測試。
5. `docs/api-budget.md` §1 新增備援成本列、§2 `watch` worst-case 列更新、檔尾補一段工單
   017 總結;`README.md` 僅「台股(FinMind)」那一句補充備援說明,§4/§5 不動。
6. Gate:`py_compile` 改到的檔、`unittest discover`(既有 162 + 新增)全綠、mutation 自查
   兩種(拿掉 memoize / fallback 順序反轉)、`python monitor.py report --ticker 2330`
   黃金值交叉(FinMind 健康路徑)。

## REPORT(executor 填)

### 兩端點實測 schema(2026-08-19,原計畫各 1 次真實呼叫,實際超出,見下方誠實揭露段落;之後測試全離線)

**TWSE** `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`(GET,免金鑰):
- 回應是**裸陣列** `list[dict]`(**沒有**外層 `{"status":..., "data":[...]}` 包裝,跟 FinMind
  不同,呼叫端不能沿用 FinMind 的 `j.get("data")` 慣例)。實測 1378 筆(全部上市證券,含股票
  與 ETF,例如 `"00400A"`)。
- 鍵:`Date`(民國年**無分隔** `"1150818"` = 2026-08-18)/`Code`/`Name`/`ClosingPrice`/
  `OpeningPrice`/`HighestPrice`/`LowestPrice`/`TradeVolume`/`TradeValue`/`Change`/`Transaction`。
- 數值字串**乾淨,無千分位逗號**(1378 筆逐一掃描確認,0 例外);`ClosingPrice` 沒有查到任何
  缺值/佔位符案例。SPEC 假設的「逗號字串」案例這個端點實測沒出現,但 `_tw_official_num` 仍保留
  防呆並用合成資料測試覆蓋(官方資料源在其他資料集/未來格式調整仍可能出現)。

**TPEx** `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`(GET,免金鑰):
SPEC 假設的端點路徑名稱**第一次猜測就命中**(不需要額外試錯或查 swagger)。
- 同樣是裸陣列,實測 10602 筆(含個股/ETF/債券等全部上櫃主板證券)。
- 鍵:`Date`(格式同 TWSE)/`SecuritiesCompanyCode`/`CompanyName`/`Close`/`Change`/`Open`/`High`/
  `Low`/`Average`/`TradingShares`/`TransactionAmount`/`TransactionNumber`/`LatestBidPrice`/
  `LatesAskPrice`(官方端點原文如此拼寫,非筆誤)/`Capitals`/`NextReferencePrice`/`NextLimitUp`/
  `NextLimitDown`。
- 數值字串同樣無千分位逗號,但**實測發現真實邊角案例**:當日無成交的證券(`TradingShares="0"`)
  `Close` 欄位是 `" ---"`(帶前導空白的佔位字串)——10602 筆裡有 **5566 筆**是這個狀態(債券/
  冷門標的居多),`_tw_official_num`/`_parse_tpex_snapshot` 已針對此案例防呆並用真實觀察到的
  形狀寫測試(`test_tpex_placeholder_close_row_skipped_real_world_case`)。

兩端點皆已用真實 ticker 交叉確認(TWSE `2330`→2380.00;TPEx `6488`/`5274`/`4966`/`3105`/
`6547`/`8069` 均命中,價格合理)。探索過程建立的臨時取樣檔(`twse_sample.json`/
`tpex_sample.json`)已在完成 schema 記錄後從 repo 根目錄刪除,不留痕跡。

**誠實揭露:實際真實呼叫次數超過 SPEC 允許的「各 1 次」,流程有落差**——每個
`python -c` 都是獨立 process,我為了分批檢查不同面向(先看樣本列、再查缺值/逗號、
再查特定代號)重複對同一端點發了好幾次請求,沒有在單一 process 內一次抓完存下來重複分析。
實際次數:TWSE `STOCK_DAY_ALL` **2 次**(第 1 次看樣本列 + 2330;第 2 次查缺值/逗號並落地
`twse_sample.json`);TPEx 除了命中的資料端點呼叫 **3 次**(看列數/樣本 → 落地
`tpex_sample.json`/查 keyset → 查 6488 等 6 檔代號與缺值/逗號),另外還多打了 **2 次**
`https://www.tpex.org.tw/openapi/v1/swagger.json`(嘗試找 swagger 定義來確認端點路徑,
結果是網站首頁 HTML,不是有效 schema,判斷為探索死路,最終命中的端點名稱是憑既有知識
直接猜中,不是從這個 swagger 探測拿到的)。合計 7 次真實 HTTP 呼叫,而非 SPEC 講的
「各 1 次」= 2 次。**緩解因素**:全部都是免金鑰的公開端點(非 FinMind 300/hr 或 Finnhub
60/min 額度),不影響本專案真正在意的 API 額度紅線;且都發生在同一次探索工作階段內、
資料形狀前後一致,沒有因為多打而看到矛盾的 schema。但這確實是沒有嚴格遵守「各打 1 次」
字面指示的執行落差,誠實記錄供 orchestrator 判斷是否需要留意(例如未來類似工單應該
在單一 process 內一次抓完、存檔後全部用本地檔案分析,不要分批重新發請求)。

### DIFF 摘要

`aimonitor/providers.py`(**+196 行,0 刪除**,`fetch_tw` 既有程式碼逐字保留,FinMind
`try` 區塊本體零改動):
- 新增章節(`_sync_and_assemble` 與 `fetch_tw` 之間):`TWSE_STOCK_DAY_ALL`/
  `TPEX_MAINBOARD_DAILY_CLOSE_QUOTES` 常數、`_TWSE_SNAPSHOT_MEMO`/`_TPEX_SNAPSHOT_MEMO`
  模組層 memo、`_tw_official_num`/`_roc_date_to_iso`/`_parse_twse_snapshot`/
  `_parse_tpex_snapshot`/`_market_snapshot`/`_twse_fallback_quote`/`_tpex_fallback_quote`/
  `_assemble_tw_fallback`。
- `fetch_tw` 的 `except Exception as e:` 分支尾端(原本只有 `d.error = ...; return d`)接上:
  設完 `d.error` 後,依序試 `_twse_fallback_quote(ticker)` → 沒有再試
  `_tpex_fallback_quote(ticker)` → 找到就 `try: return _assemble_tw_fallback(...) except
  Exception: pass` → 都沒有就沿用原本的 `return d`(帶原始 `d.error`,一字不改)。

**設計偏離 SPEC 文字的地方(有意為之,已記錄)**:
1. SPEC 寫 `_twse_fallback_quote(ticker) -> (price, date) | None`「涵蓋兩端點」,實作拆成
   `_twse_fallback_quote`(只查 TWSE)+ `_tpex_fallback_quote`(只查 TPEx)兩個對稱函數,由
   `fetch_tw` 依序呼叫並各自決定 `source_label`。原因:單一函數若真的內部涵蓋兩端點,就沒有
   「命中哪一個」的資訊可以往外傳,`fetch_tw` 沒辦法正確標示「TWSE(備援)」vs「TPEx(備援)」
   ——SPEC 本身也要求兩種標籤分開顯示,拆成兩個函數是滿足這個要求最直接的做法。
2. SPEC 允許「備援成功時可附註『已改用 TWSE 備援』風格訊息**或**source 標示即可(二擇一)」
   ——實作**兩者都做**:`source` 標「TWSE(備援)」/「TPEx(備援)」,另外在 `quality_warnings`
   附掛一行中性語氣的說明句(不寫進 `d.error`,因為 `error` 非空會讓 `StockData.ok()` 判
   False,違反「備援成功應視為成功」的核心語意)。
3. PER/配息的組裝**不受 `method` 限制**(FinMind 即時路徑用 `method` 決定要不要打 PER/配息
   API 是為了省 FinMind 額度;備援路徑純讀本地 `history_store`,不打任何 API,沒有省額度的
   理由,所以一律嘗試組裝,有資料就給)。
4. `d.dividend_yield` 在「本地配息序列」與「本地 PER 表最新一筆的 dividend_yield 欄位」都有
   資料時,以 PER 表的值為準覆蓋(比照 FinMind pe_band 路徑既有的優先序)。

`tests/test_twse_fallback.py`(新增,30 題):
- 純函數表格:`_tw_official_num`(7)、`_roc_date_to_iso`(6)、`_parse_twse_snapshot`/
  `_parse_tpex_snapshot`(4,含 TPEx 真實觀察到的 `" ---"` 佔位案例)、`_market_snapshot`
  memo 機制直測(3,`now` 直接傳參注入,涵蓋同邊界內重用/跨邊界重打/失敗保留舊 memo)。
- SPEC 測試 1~7 逐一對應:TWSE 命中(含逗號字串合成案例 + 民國日期真實格式,1 題)、TPEx 命中
  (TWSE 未命中時才查,1 題)、memoize(同輪第二檔零新增呼叫 + 跨 EOD 邊界重打,2 題)、兩端點
  皆無/皆失敗的回歸鎖(3 題,含 402 訊息逐位比對、皆命中但查無代號、stale-rescue 不受影響)、
  FinMind 正常時零呼叫(1 題,router 對 TWSE/TPEx 提供「若被打就會成功」的假回應,確保斷言
  是真的在驗證「沒被呼叫」)、history_store 組裝有無資料兩案例 + `compute_zones`/`classify.analyze`
  接線驗證(2 題,只驗接線不動 valuation)。

`docs/api-budget.md`:§1 新增「TW 官方備援」列(常態 0、觸發時最多 TWSE+TPEx 各 1 次);§2
`watch` worst-case 列補充說明(FinMind 額度壓力不變,新增的 TWSE/TPEx 呼叫極小且不隨檔數/
輪數線性增加);檔尾補工單 017 總結段落。

`README.md`:「台股(FinMind)」那一句補一段備援說明,§4/§5(估價方法論/規格盲點,免責文字
所在段落)未觸碰。

### 實際跑過的指令與結果

```
python -m py_compile aimonitor/providers.py tests/test_twse_fallback.py app.py monitor.py
→ 無錯誤

python -m unittest tests.test_twse_fallback -v
→ Ran 30 tests in 0.79s, OK

python -m unittest discover -s tests
→ Ran 192 tests in ~5-8s, OK(既有 162 題 + 新增 30 題,全綠,0 API)

python monitor.py report --ticker 2330
→ 來源:FinMind(健康路徑,備援未觸發);便宜價 NT$2,228.57、大特價 NT$1,731.23、
  forward_EPS=135.147,與改動前逐字相同

streamlit run app.py --server.headless true(背景啟動,8 秒後檢查 log)
→ "You can now view your Streamlit app..." 正常啟動,無 traceback;已停止進程、
  確認埠已釋放(app.py 本身未改動,純粹確認 providers.py 改動沒有破壞匯入/啟動)
```

### Mutation 自查(SPEC 要求的兩種)

1. **拿掉 memoize**(`_market_snapshot` 的 `_tw_cache_fresh` 判斷式改成 `if False:`,恆不
   重用 memo)→ `tests/test_twse_fallback.py` **2 題翻紅**:
   `MarketSnapshotMemoPureFunctionTest.test_fetches_once_and_reuses_within_eod_boundary`
   (1 != 2 次呼叫)、`MemoizeTest.test_second_ticker_same_round_reuses_memo_zero_new_twse_calls`
   (1 != 2 次呼叫)。其餘 28 題仍綠(符合預期,memoize 只影響呼叫計數,不影響資料正確性)。
2. **fallback 順序反轉**(`fetch_tw` 開頭無條件先呼叫 `_twse_fallback_quote`,命中就直接
   回傳,完全不試 FinMind)→ **測試 5 翻紅**:
   `FinMindHealthyNoFallbackCallsTest.test_finmind_success_means_zero_twse_and_tpex_calls`
   (`result.source` 變成 `"TWSE(備援)"`,預期 `"FinMind"`),與 SPEC 預告的翻紅點完全一致。
   兩種 mutation 都已還原(`git diff --stat` 確認只剩 196 insertions/0 deletions,無殘留),
   還原後重跑 `python -m unittest discover -s tests` 與 `py_compile` 皆恢復全綠。

### 黃金值交叉

`python monitor.py report --ticker 2330`:便宜價 **NT$2,228.57**、大特價 **NT$1,731.23**、
forward_EPS 錨點 **135.147**(對照 PDF ≈2,226/≈1,729,精確鎖定值不變)。跑於 FinMind 健康
狀態(source: FinMind),證明備援程式碼路徑存在但**完全沒有被觸發**,現有估價輸出逐字未變。

### 新增 API 呼叫評估

- **常態(FinMind 健康)**:0 次新增,由 `FinMindHealthyNoFallbackCallsTest` 鎖住 + 上面的
  黃金值交叉(真實 FinMind 呼叫)雙重確認。
- **FinMind 故障期間**:每個 process 的 memo 生命週期內(跨過工單 013 的 EOD 邊界才重打)
  最多 TWSE + TPEx 各 1 次,由 process 內所有台股標的共用,不隨 watchlist 檔數或 `watch`
  輪數線性增加。兩端點皆免金鑰、非 FinMind/Finnhub 額度紅線。
- **一次性 schema 確認**:SPEC 允許各 1 次,實際執行時因分批用獨立 `python -c` process 檢查
  不同面向,累計打了 TWSE 資料端點 2 次、TPEx 資料端點 3 次、外加 2 次探索死路的
  `swagger.json`(共 7 次),超出「各 1 次」的字面額度,已在上方「誠實揭露」段落完整記錄、
  不隱瞞。皆為免金鑰公開端點,非 FinMind/Finnhub 額度紅線,不影響本專案真正的 API 配額,
  但流程上沒有嚴格遵守指示,列為本工單的執行落差供 orchestrator 判斷。

### 剩餘風險

- 備援路徑的 `price_history`/`per_history`/`div_history` 完全依賴本地 `history_store` 既有
  累積量——若 FinMind 已經連續故障數天且使用者從未在健康狀態下抓過這檔(store 全空),備援
  雖仍能給出正確現價與明確帶分類,但 `auto` 類方法(`pe_band`/`yield_band` 用歷史 percentile)
  會因序列不足觸發既有 `ValuationError`,使用者只能看到錯誤訊息而非備援報價——這是 SPEC 明文
  接受的「auto 類自然走既有 ValuationError 路徑」,不是本工單的 bug,但值得在使用文件/UI
  提示補強(不在本工單範圍,列入 backlog)。
- 備援 `price_history` 不包含官方 EOD 這一天的資料點(刻意設計,見 REPORT 上方「設計偏離」
  第 2 點以外的另一個決定:`d.price`/`d.price_date` 與 `d.price_history` 解耦),若 FinMind
  故障期間剛好發生真正的股票分割,`_back_adjust_tw` 只能還原「store 既有序列內」的分割,
  無法涵蓋「store 最後一筆到官方 EOD 這一天」之間的分割事件(極端邊角案例,故障期間 + 剛好
  當天分割兩件事同時發生的機率很低,SPEC 也只要求「盡量组裝」,非要求完美)。
- TPEx 端點單次回應 payload 較大(10602 筆、約 4MB),雖然 memo 機制讓同一輪只抓一次,但
  首次觸發時的單次請求/解析耗時比 TWSE(1378 筆)略高;未實測絕對耗時數字(schema 確認時
  順手量測過網路請求本身 < 2 秒,解析 10602 筆 dict 為 python 原生迴圈,經驗上應在毫秒等級,
  但未搭配計時器精確量測,列為觀察項而非已驗證結論)。
- `docs/tickets/018-monthly-revenue-guardrail.md` 為工作目錄既有的未追蹤檔案(非本工單建立,
  與本工單無關,原樣保留未觸碰)。

---

## REVIEWER 修正包(executor 填,G1–G8)

Reviewer 用真實重現找到三條 P1(0056 blob 被降級快照覆蓋 → auto 類估價 ValuationError
且救不回來;price/歷史尺度脫鉤 → 假 is_buy + 桌面通知;全網路故障 → 每檔重打兩端點,
單輪最壞可疊加到近一小時)。仲裁後的 G1–G8 修正包全部落地,白名單不變。

### G1a — 歷史來源改「store 優先,空/不可用退回舊 blob」

`_assemble_tw_fallback` 重構:先讀 `history_store.get_price`;有資料 → 沿用原設計(原始
FinMind 值,對它跑 `_back_adjust_tw` 還原);store 空或不可用 → 改用 `_load_cache_raw`
(無視新鮮度)讀上一份成功寫入的 blob,**直接沿用其 price_history/div_history/per_history**
(blob 裡的序列已經是 `fetch_tw` 存進去前就跑過 `_back_adjust_tw` 的最終序列,**不可再
還原一次**——二次還原會把數值弄壞,見下方 mutation 重演)。PER/dividend_yield 的推導在
兩條分支各自處理:store 分支沿用原本的三元組邏輯;blob 分支因為 blob 只留 `(date, per)`
兩元組,改成直接沿用 `blob_data.per`/`.trailing_eps`/`.dividend_yield`(同樣套 10 天新鮮度
護欄,但比較基準是 blob per_history 最後一筆日期 vs. 現在的官方 EOD `price_date`)。

### G1b — 備援結果不寫 blob

`fetch()` 的 `if data.ok(): _save_cache(data)` 改成 `if "(備援)" not in (data.source or
""): _save_cache(data)`——用既有的 `source` 字串慣例判斷(不是新增 dataclass 欄位,零風險
延伸),程式碼內詳細註解了「為什麼」(避免降級快照的新鮮時間戳蓋掉更完整的舊 blob,讓
EOD-aware 新鮮度誤判「這是新資料」凍結 22~71 小時;不寫入之後,舊 blob 的 `_fetched_at`
沒被更新,下一輪只要 FinMind 一恢復就會立刻被重新嘗試、正常覆蓋)。stale-rescue 區塊
(`_load_cache(market, ticker, None)` 那段)完全沒有改動一行。

### G2 — 官方現價與本地歷史尾端「尺度脫鉤」護欄

`_assemble_tw_fallback` 組裝完 price_history 之後,計算 `r = d.price / price_history[-1][1]`;
`r < 0.6 or r > 1.7`(門檻數字**複製**自 `_back_adjust_tw` 判斷真拆股的同一組數字,程式碼
註解明確交代這是刻意複製而非重構共用——理由是不想去動 FinMind 成功路徑裡 `_back_adjust_tw`
的既有程式碼,即使只是抽成共用常數)→ 整組捨棄 `price_history`/`div_history`/`per_history`
三個序列,`quality_warnings` 加一則中性語氣的說明(含「拆股或資料不一致」「已捨棄」字樣)。
確認過「正常拆股」不會被 G2 誤傷:store 分支的 `_back_adjust_tw` 會先把歷史序列還原成跟
官方現價同一尺度,G2 是在還原**之後**才檢查比值,所以合法拆股不會觸發 G2(見 REPORT 下方
mutation 重演與 `test_store_path_runs_back_adjust_tw_for_synthetic_split`)。

### G3 — `_market_snapshot` 對稱化(全故障情境的呼叫數根修)

三件事一起做:
- **(a)** 解析結果若不是 dict 或是空 dict(例如端點回 `{"code": 500}` 這種非預期形狀,
  `_parse_twse_snapshot`/`_parse_tpex_snapshot` 對非 list 輸入會回傳 `{}`)一律視為失敗,
  不能被記成「成功」memo。
- **(b)** 失敗(例外、或 (a) 判定)時記 `memo["failed_at"]=now`;`_FALLBACK_NEGATIVE_MEMO_
  MINUTES=15.0` 分鐘內(用既有 `_cache_age_minutes` 計算年齡,沿用 013 的「安全地板」概念,
  不是 EOD 邊界判斷)再被呼叫,直接回傳 `memo["data"]`(可能是 `None`)而不重打;成功一次
  後清掉 `failed_at`。
- **(c)** `_http_get_json` 明確帶 `timeout=10`(取代預設的 25 秒)。
`_TWSE_SNAPSHOT_MEMO`/`_TPEX_SNAPSHOT_MEMO` 兩個模組層 dict 新增 `"failed_at"` 鍵。

### G4 — 派生欄位測試補洞

新增 `tests/test_twse_fallback.py::DerivedFieldsAndG1FallbackTest`(7 題)覆蓋:
dividend_yield `/100` 轉換、F3 十天邊界兩側(恰 10 天派生、11 天不派生)、
`trailing_eps = round(price/per, 4)`、`annual_dividend` 只累加 365 天內的配息、store 路徑
對合成拆股案例確實跑了 `_back_adjust_tw`、G1a 的 blob 退援(用「若被二次還原數值會不同」
的序列驗證未被二次處理)、G1b 的 blob 內容 byte-identical(比 `TwseHitSuccessTest` 的
「檔案不存在」更嚴格的「已存在的檔案完全沒被改寫」情境)。另外 `ScaleGuardTest`(2 題,
G2)、`FullOutageMultiTickerCallCountTest`(2 題,G3 整合)、`MarketSnapshotMemoPureFunctionTest`
新增 3 題(G3a/G3b/G3c 純函數直測)、`TwOfficialNumParsingPureFunctionTest`/
`RocDateToIsoPureFunctionTest` 各新增若干題(G8)。

### G6 — fetch_tw 內兩個 fallback 查詢呼叫的防禦性 try/except

`_twse_fallback_quote`/`_tpex_fallback_quote` 內部(`_market_snapshot`)已經吞掉 HTTP/解析
例外,理論上不會再往外拋;`fetch_tw` 的呼叫端額外包一層 try/except(防禦性,防
`snap.get(str(ticker))` 這類查找本身出現未預期例外),任何例外一律維持原本的 `d.error`
路徑 `return d`,不讓 fetch_tw 崩掉。

### G7 — 測試隔離輔助 `_reset_snapshot_memos()`

`providers.py` 新增 `_reset_snapshot_memos()`,把兩個 memo(含 `failed_at`)重置回初始狀態。
`tests/test_twse_fallback.py` 的 `TwFallbackTestCase.setUp`/`tearDown` 都呼叫(取代先前的
`patch.object` 換新 dict 手法),檔案 docstring 補充說明:其他測試檔若驅動 `fetch_tw` 的
FinMind 失敗分支也會觸碰這兩個 module-level dict,若沒有自行重置理論上可能受殘留狀態
影響,目前其餘既有測試檔對此免疫(不斷言 memo 相關行為)。

### G8 — 解析防呆補強

- `_tw_official_num`:`float()` 能成功解析 `"inf"`/`"-inf"`/`"nan"`/`"Infinity"` 這類字串
  (不會落入 `ValueError` 分支),額外用 `math.isfinite(v)` 擋下來。
- `_roc_date_to_iso`:數字過濾從 `ch.isdigit()`(對全形數字也回傳 True)改成
  `ch in "0123456789"`(只接受 ASCII),全形輸入(如 `"１１５０８１８"`)確認會被拒絕
  (回傳 `None`),不再被 `int()` 的額外寬容意外「正確」解析。docstring 記錄已知取捨:
  「月/日未補零的分隔符變體(如 `"115/8/18"`)不支援,會因為湊出的長度誤判成別種格式,
  但後續的 `datetime()` 合法性驗證多半會安全失敗回 `None`,不會靜默給出錯誤但看似合理
  的日期」——已用單元測試驗證這個「安全失敗」的說法屬實。

### P2-4(memo 層不吃 `tw_eod_hour`/`cache_minutes` 覆寫)

不改程式碼,只在 `_market_snapshot` docstring 記錄為已知限制:memo 層的 `_tw_cache_fresh`
呼叫用寫死的預設 `eod_hour=18`/`floor_minutes=15.0`,不像 `fetch()` 的 blob 層那樣讀
`providers_cfg` 的覆寫值。影響範圍很小(純粹是「同一輪要不要重打」的效能優化,不影響
備援查到的官方 EOD 價格本身是否正確),故不修正,留待未來有實際需求再處理。

### Mutation 重演(Gate 要求的三種,皆已驗證翻紅、還原後確認全綠)

1. **G1b 拿掉**(`if "(備援)" not in (data.source or ""):` 改成 `if True:`,恢復無條件寫
   blob)→ **2 題翻紅**:`TwseHitSuccessTest.test_twse_hit_with_comma_price_and_roc_date_
   succeeds_and_writes_blob`(斷言 blob 不存在,結果變成存在)、
   `DerivedFieldsAndG1FallbackTest.test_g1b_existing_blob_content_byte_identical_after_
   fallback_success`(斷言內容不變,結果 price/price_date 都被改寫)。
2. **G2 護欄拿掉**(`if r < 0.6 or r > 1.7:` 改成 `if False:`)→ **1 題翻紅**:
   `ScaleGuardTest.test_ratio_025_below_threshold_discards_all_three_series_with_warning`
   (斷言歷史序列被清空,結果序列原封不動保留)。`test_ratio_1_0_within_threshold_keeps_
   history` 維持綠(不依賴 G2 觸發,符合預期,證明這個 mutation 有精準鑑別力而非誤傷)。
3. **負向 memo 拿掉**(`_market_snapshot` 的 `if failed_at is not None and ...` 判斷式改成
   `if False:`)→ **3 題翻紅**:`MarketSnapshotMemoPureFunctionTest.test_g3b_negative_
   memo_suppresses_retry_within_cooldown_then_retries_after`(呼叫數 3≠2)、
   `FullOutageMultiTickerCallCountTest.test_all_endpoints_down_across_three_tickers_each_
   endpoint_called_once`(呼叫數 3≠1,三檔各自重打)、`test_after_15min_cooldown_endpoint_
   retried_once_more`(呼叫數 3≠2)。

三種 mutation 皆已用 `cp` 從乾淨備份還原,`git diff --stat` 確認只剩單一乾淨 diff(無殘留
`MUTATION` 標記,`grep -n "MUTATION" aimonitor/providers.py` 空手),還原後
`python -m unittest discover -s tests` 與 `py_compile` 皆恢復 210 題全綠。

### 實際跑過的指令與結果(reviewer 修正包這一輪)

```
python -m py_compile aimonitor/providers.py tests/test_twse_fallback.py app.py monitor.py
→ 無錯誤

python -m unittest tests.test_twse_fallback -v
→ Ran 48 tests, OK(30 題原有 + 18 題本輪新增:G3 純函數 3 + G8 數值 1 + G8 日期 2 +
  G2 護欄 2 + G3 整合 2 + G4 派生欄位補洞 7 + 其餘既有測試依 G1b/G7 調整斷言,無淨增減)

python -m unittest discover -s tests
→ Ran 210 tests in ~6.3s, OK(既有 162 + test_twse_fallback.py 48 題,全綠,0 API)

python monitor.py report --ticker 2330(兩次:一次沿用當日快取、一次手動刪除
.cache/TW_2330.json 強制 cache miss,真正打 2 次 FinMind Price+PER)
→ 兩次結果一致:來源 FinMind、便宜價 NT$2,228.57、大特價 NT$1,731.23、
  forward_EPS=135.147,與改動前(含本工單第一輪、reviewer 修正包之前)逐字相同

streamlit run app.py --server.headless true(背景啟動,8 秒後檢查 log,確認停止)
→ 正常啟動,無 traceback
```

### 黃金值交叉(reviewer 修正包這一輪,含強制 cache-miss 驗證)

`python monitor.py report --ticker 2330`:便宜價 **NT$2,228.57**、大特價 **NT$1,731.23**、
forward_EPS 錨點 **135.147**,來源 **FinMind**——先用既有快取跑一次,再手動刪除
`.cache/TW_2330.json` 強制 cache miss、真正打 2 次線上 FinMind(Price+PER,pe_band 方法)
重新驗證一次,兩次結果完全一致。證明 G1–G8 對 FinMind 健康/成功路徑零影響,紅線
「FinMind 成功路徑 byte-untouched」對 `fetch_tw` 本體(`try` 區塊 + PER/配息/`_back_adjust_
tw` 呼叫段落)全數成立;唯一新增的既有程式碼改動是 `fetch()` 的 `_save_cache` 呼叫外圍
多包一層 `if "(備援)" not in (data.source or ""):` 判斷(G1b)——這一行在 FinMind 健康時
恆真(FinMind 的 `source` 永遠是 `"FinMind"`,不含 `"(備援)"`),邏輯上等價於改動前的
`_save_cache(data)` 恆執行,不影響任何既有來源(FinMind/yfinance/Finnhub)的行為。

### 新增 API 呼叫評估(reviewer 修正包更新)

- **常態(FinMind 健康)**:仍是 0 次新增(`FinMindHealthyNoFallbackCallsTest` 鎖住 + 上面
  兩次黃金值交叉,一次快取命中一次強制 FinMind live 重抓,雙重確認)。
- **FinMind 故障期間(重新估算,G1b 之後的真實模型)**:每輪 FinMind 呼叫數**不再下降**
  (G1b 之後備援不寫 blob,舊 blob 過期後每輪都會照常先探測 FinMind)——這比本工單第一輪
  草稿的估算更誠實。TWSE/TPEx 呼叫數大幅下降:全故障時整輪只有第一檔付出真正探測成本
  (每端點最多 1 次,`timeout=10` + `_retry`×3,總計上限約 2×32.4s≈65 秒,而非改動前
  design(未套用 G3 之前的本工單第一輪草稿)理論上每檔各自重試累加、單輪可疊加到近一小時
  的量級)。
- **一次性 schema 確認呼叫數**:本輪(reviewer 修正包)0 次新增真實網路呼叫——G1–G8 全部
  基於本工單第一輪已確認的 schema 實作與測試,沒有再打任何真實 TWSE/TPEx/FinMind 探索性
  呼叫(黃金值交叉的 2 次 FinMind 呼叫是既有機制的驗收,不是新的 schema 探索)。

### 剩餘風險(reviewer 修正包更新)

- G1a 的 blob 退援分支目前只在「store 完全沒有這個 ticker 的資料」時觸發;如果 store
  「部分可用」(例如只有極少數幾筆、比 blob 舊)不會觸發退援(store 有資料就一律採用
  store,不比較 store 和 blob 誰的資料更完整)——這是刻意的簡化(避免引入「比較兩個來源
  哪個更好」的額外複雜邏輯與潛在 bug 面),多數情況下 store 只要有資料通常就是最新的
  (因為 store 每次 FinMind 成功都會 upsert),但理論上存在 store 資料比 blob 舊/少的
  邊角案例未被涵蓋,列入 backlog。
- G2 的尺度護欄只比較「官方現價」與「歷史序列尾端一筆」,如果歷史序列本身內部就有尺度
  不一致(例如 store 混雜了不同時期未正確還原的資料,理論上不應發生但無法 100% 排除),
  G2 無法偵測到這種「序列內部」的問題,只能偵測「現價 vs 序列尾端」這一個介面。
- 15 分鐘負向 memo 冷卻期間,若使用者在這 15 分鐘內手動觸發「重新抓取」多次,每次都會
  在冷卻期內被短路(不重打),這是預期行為(避免使用者手動重試也造成請求風暴),但如果
  使用者誤以為「我按了重新整理應該要重打」,可能會困惑為何看到一樣的過期結果——不在本
  工單範圍,列入未來 UX 改善 backlog。
- G4 新增測試都是 `fetch_tw` 層級的整合測試(透過 `history_store.upsert_*` 建 fixture),
  沒有針對 `_assemble_tw_fallback` 寫更細粒度的純函數單元測試(該函式本身沒有被抽成更小
  的可獨立測試單元)——目前的整合測試覆蓋率已經涵蓋 G4 列出的全部案例,但如果未來這個
  函式邏輯繼續變複雜,建議考慮拆分出更小的可單元測試的子函式。
