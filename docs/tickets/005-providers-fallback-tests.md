# 005 — 資料源降級鏈離線 mock 測試(providers.fetch)

狀態:**CLOSED**(2026-07-06,commit 見下方收斂紀錄)

## 收斂紀錄(orchestrator)

- Executor 交付 13 測試(9 場景),自主做了 10 項 mutation 驗證(10/10 翻紅)。
- Reviewer(從嚴)結論:**無 P1**——mock 介面保真度逐欄位吻合(yfinance 簽章、
  FinMind/Finnhub 回應形狀、URL router 無碰撞),mutation 抽查 3/3 屬實,
  白名單乾淨、真實 `.cache/` 未污染。仲裁:
  - **P2-1 採納**:S1 的 `assertNotIn("yfinance", sys.modules)` 有環境耦合誤報風險
    (process 內任何人 import 過 yfinance 就誤報)→ 改為注入「Ticker 一被呼叫就
    raise AssertionError」的假模組,直接證明 cache-hit 路徑未觸碰 yfinance。
  - **P2-2 採納**:三處 `os.environ.pop("FINNHUB_API_KEY")` 無還原 → base class
    setUp 加 `patch.dict(os.environ)` 快照,tearDown 自動還原,一處修全部。
  - **P3-1/P3-2 採納(誠實修正涵蓋宣稱)**:S5 實際只走「無金鑰 → yfinance 失敗
    → stale rescue」路徑,**Finnhub 分支失敗後的 stale rescue 未被走到**;REPORT
    「線上兩源都失敗」措辭不精確(該測試自己斷言 mock_http.call_count==0)。
    rescue 邏輯在 fetch() 層與上游源無關,覆蓋風險低,故不加測試、僅在此修正紀錄。
  - **P3-3 採納**:S9 兩測試 `reset_mock()` 防未來 sleep 計數串接。
- **引擎觀察(不修,留紀錄)**:過期快取保命不看 `use_cache` 旗標(providers.py:379);
  現況所有呼叫點都用預設 True(008 之後含 watch),無實際影響;未來若做
  「強制刷新」功能須注意此語意並屆時開單。
- 收斂 gate:71/71 綠(0.05s,離線)、py_compile 綠、diff 僅白名單、
  orchestrator 已逐行讀 diff、真實 .cache/ 逐位元組未動。→ CLOSE。
母單:000-baseline.md 工單 005。此鏈是雲端存活的命脈(README ☁ 節),測試設計錯會給假安全感。

## SPEC(orchestrator)

### 目標
用 unittest.mock 鎖住 `aimonitor/providers.py` 的降級順序與錯誤語意,**零真實網路**。

### 降級鏈現狀(對照 providers.py,測試鎖此行為)
```
fetch(use_cache=True) ── 新鮮快取 hit ──→ 直接回傳(0 網路)
  └ miss → TW: fetch_tw(FinMind;method 決定 Price/PER/Dividend 呼叫數)
         → US: 有 finnhub_api_key → fetch_us_finnhub(quote 失敗/c=0 → 整檔退回 fetch_us)
                                     (quote 成功 → best-effort yfinance 歷史,失敗靜默略過)
               無金鑰 → fetch_us(yfinance,_retry×3)
         → INTL: fetch_us
  └ data.ok() → _save_cache;否則 → _load_cache(max_age=None) 過期快取保命
                (source 加「(過期快取)」);再無 → 回傳含 error 的 data
```

### 允許檔案(白名單)
- 新增 `tests/test_providers_fallback.py`(stdlib unittest + unittest.mock,零新依賴)

### 必守的 mock 紀律
1. **快取隔離**:每個測試把 `providers.CACHE_DIR` patch 到 tempfile 目錄
   (setUp 建立、tearDown 清除),**絕不能讀寫使用者真實 `.cache/`**。
2. **保險絲**:patch `providers.urllib.request.urlopen` 為「一被呼叫就 raise AssertionError」,
   證明所有網路出口都被更上層的 mock 攔住(`_http_get_json` 才是主要 mock 點)。
3. **yfinance**:`fetch_us` 是函數內 `import yfinance as yf` → 用
   `unittest.mock.patch.dict(sys.modules, {"yfinance": fake_module)` 注入假模組
   (fake `Ticker` 類,`history()`/`.info` 可設定成功/raise)。
4. **速度**:patch `providers.time.sleep`(_retry 指數退避 0.8/1.6s),測試套件必須仍在
   秒級完成。

### 測試場景(每項標 oracle)
1. **快取 hit → 0 網路**:先用 `_save_cache` 寫新鮮資料,fetch() 回快取;
   `_http_get_json` 與 yfinance 呼叫數 == 0。
2. **US 有金鑰、Finnhub 成功**:mock `_http_get_json` 依 URL 分流(/quote→{"c":123.0,"t":...}、
   /metric→{"metric":{...}});yfinance 注入為 history 會 raise 的假模組 →
   結果 ok()、source=="Finnhub"、price==123.0、price_history==[](歷史缺不致命);
   並驗證有寫入快取(temp CACHE_DIR 出現檔案)。
3. **Finnhub quote c=0 → 整檔退回 yfinance**:/quote 回 {"c":0} + 假 yfinance 成功
   → source=="yfinance"、price 來自假歷史。Finnhub 例外(raise)同樣退回(兩個 case)。
4. **US 無金鑰 → 直接 yfinance**:`_http_get_json` 呼叫數 == 0(證明沒偷打 Finnhub)。
5. **全源失敗 + 過期快取保命**:temp 快取放一筆 `_fetched_at` 兩天前的舊資料;
   Finnhub raise + yfinance raise → 回傳舊資料、ok()==True、source 含「(過期快取)」。
6. **全源失敗 + 無快取**:回傳 ok()==False、error 非空。
7. **TW FinMind 402**:mock raise RuntimeError("HTTP 402 …") → d.error 含「402」與
   「FINMIND_TOKEN」字樣(鎖 02a6787 的清楚錯誤訊息)。
8. **TW 呼叫數 = api-budget §1 表**:以 `_http_get_json` 呼叫次數斷言——
   pe_band→2(Price+PER)、price_band→1、yield_band→2(Price+Dividend)。
   這把 004 審計的數字變成可執行的 regression。
9. **_retry 語意**(對抗 429 的核心):第 3 次才成功 → 回傳成功且 sleep 被呼叫 2 次;
   3 次全失敗 → raise 最後一個例外。

### 驗收(收斂 gate)
- `python -m unittest discover -s tests` 全綠(58 + 新增),**拔網路也能跑**、秒級完成。
- py_compile 綠;diff 僅白名單;真實 `.cache/` 目錄的 mtime/內容不被測試改動。
- 0 次 API 呼叫。

### 禁區
- 不改 `providers.py`(發現 bug → 停下寫 REPORT,由 orchestrator 決定);
  不動 config/watchlist/禁區檔;不做任何真實網路呼叫。

## PLAN(executor 填)

1. 新增單一檔案 `tests/test_providers_fallback.py`(stdlib unittest + unittest.mock,零新依賴)。
2. 共用 base class `ProvidersFallbackTestCase`:
   - `setUp`:`tempfile.mkdtemp()` → `patch.object(providers, "CACHE_DIR", tmpdir)`(_cache_path 執行期讀
     模組全域,故 patch 對所有 fetch 路徑都生效);同時 patch `providers.time.sleep`(no-op,測秒級)、
     patch `providers.urllib.request.urlopen` 為 raise AssertionError("real network!")(保險絲,證明
     `_http_get_json` 才是唯一合法出口)。
   - `tearDown`:`shutil.rmtree(tmpdir, ignore_errors=True)`。
   - 額外提供 helper:`_fake_yf_module(hist_rows=None, info=None, raise_on_history=False,
     raise_on_ticker=False)` 回傳一個「假 yfinance 模組物件」(FakeTicker/FakeHist,不 import pandas,
     hist 用一個小型 class 提供 `.index`(list of 有 `.strftime` 的假 Timestamp)、`__len__`、
     `["Close"]`(list),支援 `zip(hist.index, hist["Close"])`)。
3. 9 個場景各自獨立 TestCase,每個都在 docstring/註解寫「這題鎖什麼、什麼突變會翻紅」:
   - S1 快取 hit → 0 網路(`_save_cache` 先寫新鮮資料,`fetch()` 走 cache 分支,斷言
     `_http_get_json` mock 呼叫數 == 0 且 yfinance 模組完全不被 import)。
   - S2 US 有金鑰、Finnhub 成功(含歷史缺失仍 ok):`_http_get_json` side_effect 依 URL 判斷
     `/quote` vs `/stock/metric`;yfinance 假模組 `history()` raise → best-effort 略過,
     `price_history == []`,`source=="Finnhub"`,並斷言 temp CACHE_DIR 內出現快取檔。
   - S3a Finnhub quote c=0 → 退回 yfinance(yfinance 成功假模組提供歷史,斷言 `source=="yfinance"`,
     price 取自假歷史最後一筆)。
   - S3b Finnhub 例外(`_http_get_json` raise)→ 同樣退回 yfinance(驗證 `fetch_us_finnhub` 的
     `except Exception: fh = {}` 分支與 c=0 分支殊途同歸)。
   - S4 US 無金鑰 → 直接 yfinance,斷言 `_http_get_json` 呼叫數 == 0(證明沒有偷打 Finnhub)。
   - S5 全源失敗 + 過期快取保命:temp 快取先手動寫入 `_fetched_at` 為 2 天前的 JSON blob,
     Finnhub raise(或無金鑰時 yfinance raise)→ `ok()==True`、`source` 含「(過期快取)」。
   - S6 全源失敗 + 無快取 → `ok()==False`、`error` 非空。
   - S7 TW FinMind 402:`_http_get_json` raise `RuntimeError("HTTP Error 402: ...")` →
     `d.error` 同時含 "402" 與 "FINMIND_TOKEN"。
   - S8 TW 呼叫數:`_http_get_json` side_effect 用 list 累積收到的 URL,依 `method` 斷言
     `pe_band`→2 次(Price+PER)、`""`(price_band)→1 次、`yield_band`→2 次(Price+Dividend)。
   - S9 `_retry` 語意:直接測 `_retry` 本身(不透過 fetch),第 3 次成功 → `time.sleep` 呼叫 2 次;
     3 次全失敗 → raise 最後一個例外(用可辨識的不同 Exception 實例逐一驗證是「最後一個」被 raise)。
4. Gate:`python -m unittest discover -s tests -v`(58 舊 + 新增全綠)、
   `python -m py_compile tests/test_providers_fallback.py`、比對 `.cache/TW_2330.json` mtime/size
   測試前後不變。
5. 不改 `providers.py`;若讀碼過程發現行為疑點,只寫進 REPORT,不動生產碼。

## REPORT(executor 填)

### 產出
- 新增 `tests/test_providers_fallback.py`(唯一改動檔,白名單內);另填寫本工單的
  PLAN/REPORT。**沒有**改動 `aimonitor/providers.py` 或任何禁區檔。

### Gate 結果
- `python -m py_compile tests/test_providers_fallback.py` → 通過。
- `python -m unittest discover -s tests -v` → **71 tests, OK**(58 舊 + 13 新增,
  0.040s,全綠;拔網路可跑,因為每個測試都保險絲 patch 了 `urlopen` 為
  raise AssertionError,實際跑下來從未觸發過這條保險絲)。
- `.cache/` 真實目錄完整性:測試前後 `.cache/TW_2330.json` 的 mtime
  (`2026/7/6 10:18:24`)與檔案大小(56865 bytes)**逐位元組一致**,且目錄內
  仍只有這一個檔案(測試前後各跑一次 `Get-ChildItem` 比對)。
- `git status --short` 只顯示 `docs/tickets/005-providers-fallback-tests.md`
  與 `tests/test_providers_fallback.py` 兩個檔案;`git diff --stat` 對
  `aimonitor/providers.py` 回傳空(零差異)。
- 0 次真實 API 呼叫(全程無網路存取,無 402/429/timeout 之類的痕跡)。

### 測試品質驗證(mutation testing,額外做的,非工單要求但認為必要)
工單提醒「寧可少寫一題也不要寫一題怎樣都綠的測試」,所以在 scratchpad 寫了一支
臨時 harness(未納入交付、已刪除),用 `unittest.mock.patch.object` **在記憶體內
monkeypatch** `providers` 模組的函式(完全不碰 `providers.py` 檔案本身,尊重
「不改 providers.py」的紅線),對 9 個測試裡挑出的關鍵斷言逐一注入「模擬的行為
退化」,驗證測試會確實翻紅、而非形式上通過:

| # | 模擬退化 | 目標測試 | 結果 |
|---|---|---|---|
| A | `_load_cache` 永遠回 None(快取 hit 分支失效) | S1 快取 hit | 翻紅 ✓ |
| B | `fetch_us` 內偷偷多打一次 `_http_get_json` | S4 無金鑰呼叫數==0 | 翻紅 ✓ |
| C | 假裝線上抓取「真的成功」(不該走到過期快取分支卻走了都測不出來) | S5 過期快取保命 | 翻紅 ✓ |
| D | `_retry` 拿掉重試間的 sleep | S9 重試 3 次成功、sleep×2 | 翻紅 ✓ |
| E | `_retry` 全敗時 raise 第一個例外而非最後一個 | S9 raise 最後一個例外 | 翻紅 ✓ |
| F | TW price_band 意外多打一次 PER API | S8 呼叫數 pe/price/yield | 翻紅 ✓ |
| G | Finnhub c=0 被誤判為合法報價(不退回 yfinance) | S3 c=0 退回 yfinance | 翻紅 ✓ |
| H | Finnhub 成功但歷史抓取失敗被誤判為致命錯誤 | S2 歷史缺失仍 ok() | 翻紅 ✓ |
| I | 402 錯誤訊息拿掉 "FINMIND_TOKEN" 字樣 | S7 402 錯誤訊息 | 翻紅 ✓ |
| J | 全源失敗卻被誤判成功(error 清空) | S6 全源失敗無快取 | 翻紅 ✓ |

10/10 模擬退化全部正確翻紅,且對應的「無退化」基準跑法全部維持綠燈——測試組
對這 9 個場景涵蓋的每一條分支都有實質鑑別力,不是空判斷。

### 每場景一句話結果
1. **S1 快取 hit → 0 網路**:`_save_cache` 寫入新鮮資料後 `fetch()` 直接命中,
   `_http_get_json` mock 呼叫數 0、`sys.modules` 裡完全沒有 `yfinance`。
2. **S2 US 有金鑰、Finnhub 成功(歷史缺失仍 ok)**:`/quote`+`/stock/metric`
   各打一次,yfinance 補歷史故意設定 raise → `price_history==[]` 不致命,
   `ok()==True`、`source=="Finnhub"`、`price==123.0`,且 temp CACHE_DIR 內
   確實出現 `US_AAPL.json` 快取檔(`fetch()` 的 `data.ok()→_save_cache` 分支)。
3. **S3 c=0 / 例外兩種 case 都退回 yfinance**:c=0(`_finnhub_us` 提前 return {}）
   與 `/quote` 直接 raise(未被 try 包住,冒到 `fetch_us_finnhub` 的
   `except Exception: fh={}`)兩條路徑都驗證了 `source` 變 `"yfinance"`、
   price 取自假造的 yfinance 歷史最後一筆(151.5)。
4. **US 無金鑰 → 直接 yfinance**:`_http_get_json` 呼叫數 0,證明分流邏輯
   `fh_key = ... or ...` 在雙空字串時確實走 else 分支,沒有偷打 Finnhub。
5. **全源失敗 + 過期快取保命**:用 `_save_cache` 產生真實序列化 blob 後手動把
   `_fetched_at` 改成 2 天前,線上兩源都失敗 → `ok()==True`、`price==250.0`、
   `source` 含 `"(過期快取)"`(逐字比對,對照 `providers.py:381-382` 的原字串)。
6. **全源失敗 + 無快取 → error 非空**:temp CACHE_DIR 全空、yfinance/Finnhub
   都失敗 → `ok()==False`、`error != ""`(用一個從未快取過的假代號
   `BRAND_NEW_TICKER` 確保沒有 stale 快取誤救援)。
7. **TW FinMind 402**:`_http_get_json` raise `RuntimeError("HTTP Error 402: ...")`
   → `d.error` 同時含 `"402"` 與 `"FINMIND_TOKEN"`(鎖 02a6787 的清楚錯誤訊息,
   對照 `providers.py:169-170` 原字串逐字比對兩個子字串)。
8. **TW 呼叫數 regression**:`method=""`(price_band)→1 次(只 Price)、
   `"pe_band"`→2 次(Price+PER)、`"yield_band"`→2 次(Price+Dividend),
   三個獨立測試各自成功抓取才計數(用 `assertTrue(result.ok())` 排除「因失敗
   提早中止導致呼叫數偶然正確」的偽陽性)。
9. **`_retry` 語意**:第 3 次成功 → `sleep` 恰好呼叫 2 次,且用
   `assert_any_call(0.8)` / `assert_any_call(1.6)` 鎖住指數退避的**倍率**
   (不只是次數,若退避公式被改壞也會抓到);3 次全敗 → 用可辨識的例外實例
   逐一 `assertIs`(而非只比對訊息字串)驗證 raise 的確實是「最後一個」例外。

### 引擎疑點(僅記錄,未改 providers.py)
逐行讀碼時注意到一個**非 bug 但值得知會 orchestrator 的行為**:`fetch()` 內,
當 `use_cache=True` 但新鮮快取 miss、且後續所有來源都失敗時,會**無條件**再
呼叫一次 `_load_cache(market, ticker, None)`(max_age_min=None,無視 TTL)去找
過期快取。也就是說,即使呼叫端明確傳入 `use_cache=False`(想強制刷新、不要用
任何快取),失敗時**仍然**可能吃到舊快取當保命(過期快取保命邏輯不看
`use_cache` 旗標,只看是否失敗)。目前程式碼裡似乎沒有任何呼叫點傳
`use_cache=False`(app.py/monitor.py 皆用預設 `True`),所以現況下無實際影響,
但如果未來有人加一個「強制刷新」按鈕並傳 `use_cache=False`,可能會意外看到
「明明要求不要用快取,失敗時卻還是顯示了舊資料」——這是設計選擇還是疏漏,
建議 orchestrator/使用者判斷是否要補一個「use_cache=False 時過期快取也不救」
的工單。本測試套件**沒有**針對 `use_cache=False` 寫場景(SPEC 9 個場景都沒有
要求,超出這次工單範圍),故這條路徑目前無 regression 保護。

### 剩餘風險 / 範圍外
- 未覆蓋 `INTL` 市場分支(`market=="INTL"` 直接呼叫 `fetch_us`)——SPEC 9 個
  場景未點名,且其邏輯與 US 無金鑰分支完全相同的 `fetch_us` 呼叫,風險極低,
  故未額外開場景以免無謂增加測試面積。
- 未覆蓋 `usd_twd()` 匯率函式(SPEC 未點名;它是獨立的、有自己的 try/except
  fallback,非本工單的「fetch() 降級鏈」範圍)。
- 未測試「快取檔案損毀(JSON parse 失敗)」對 `_load_cache` 的影響——
  `_load_cache` 本身有 `except Exception: return None` 兜底,SPEC 未點名此
  邊界,判斷為現有 `test_back_adjust.py` 風格之外的額外範圍,未展開。
- Windows 主控台編碼:終端機顯示中文字元有時出現亂碼(`cp950`/`cp1252` 顯示
  問題),純粹是終端顯示層,不影響測試判斷邏輯或斷言本身(斷言字串比對在
  Python 內部走 UTF-8,不受終端顯示影響)。
