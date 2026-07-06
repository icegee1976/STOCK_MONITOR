# 000-baseline — 現況盤點與工單疊(Orchestrator 第一輪)

- 日期:2026-07-06
- 分支:`tier1/restart`(乾淨,HEAD = `8e510f7`)
- 性質:**只盤點、零程式碼變更**。本檔為後續窄工單的母文件。

---

## 1. 現況(5 句)

1. 專案已完整程式化孫慶龍《AI 投資藍圖》的前瞻本益比河流圖估價法,CLI(`monitor.py`:report/screen/roi/watch/bands)與 Streamlit dashboard(`app.py`)共用 `aimonitor/` 六模組引擎(providers/valuation/classify/roi/screener/report),支援 `pe_band`/`ps_band`/`price_band`/`yield_band` 四種估價法與 57 檔清單(PDF 四檔 + AI 金字塔 + 太空)。
2. 最近 12 筆 commit 的主軸是**雲端部署韌性**:美股接 Finnhub 當 yfinance 429 的備援(`d34526d`)、`price_band` 支援固定價格帶讓雲端免歷史也能分類(`a2e556d`)、FinMind 呼叫從 75→36/載入避免 402 額度用罄(`02a6787`)、側邊欄「自帶 API 金鑰」讓分享時各用各的額度(`c091ce7`)。
3. 其前一波是**使用者體驗**:自訂清單分頁、AI 金字塔分層分頁、Windows 雙擊啟動 bat 的 cp950/Store-python 修復、總覽排序修正。
4. 最新一筆 `8e510f7` 是 Tier-1 協作腳手架(orchestrator/executor/reviewer + 工單規約),即本輪流程的起點。
5. **目前完全沒有自動化測試**,數值正確性唯一的 gate 是人工跑 `report --ticker 2330` 對 PDF 黃金值——而這需要打 FinMind API,離線黃金值 regression 測試是規約點名的最高價值first ticket。

## 2. 基準線紀錄(2026-07-06 實測)

| 項目 | 結果 |
|---|---|
| `python -m py_compile` × 11 檔(monitor.py、app.py、_add_pyramid.py、_gen_watchlist.py、aimonitor/ 7 檔) | ✅ 全部通過 |
| `python monitor.py report --ticker 2330`(FinMind,單標的,額度安全) | ✅ 正常出報表,現價 NT$2,460(2026-07-06),分類「合理價區」 |
| **黃金值:便宜價** | **NT$2,228.57**(PDF ≈2,226,差 +0.12%,README §3 範例同為 2,228 → 引擎一致,屬已知舍入) |
| **黃金值:大特價** | **NT$1,731.23**(PDF ≈1,729,差 +0.13%,同上) |
| 估值錨點 | forward_EPS = 135.147(2029),推導鏈 `2.89e12×(1+24%)^5×41.362%÷2.59e10` 完整顯示 ✅ |
| 免責聲明 | CLI 報表 footer ✅;`app.py` 頂部 caption(L284)+ 底部 footer(L727)✅ |
| Secrets | `config.yaml` 的 `finmind_token`/`finnhub_api_key` 皆空字串 ✅;金鑰走 env / Streamlit secrets / 側邊欄 ✅;`.gitignore` 涵蓋 `.cache/`、`custom_watchlist.yaml` ✅;git 追蹤 28 檔無敏感物 ✅ |

> 📌 **黃金值基準(供 regression 測試鎖定)**:引擎對 2330 目前 watchlist 假設的輸出為 便宜 2,228.57 / 大特 1,731.23(對照 PDF 2,226 / 1,729,容差建議 ±0.5%)。若未來 `watchlist.yaml` 的 2330 假設被使用者改動,測試 fixture 必須用**測試內寫死的假設副本**,不可依賴 live watchlist(紅線 3:不改使用者假設;測試也不該被使用者假設變動弄破)。

## 3. 風險與未完成點(對照紅線 / 禁區 / README §5)

依風險排序:

1. **[數值正確第一] 零自動化測試**。估價、五帶分類、ROI 稅費(證交稅 0.3%/手續費/美股股利預扣 30%/匯率)、GBM 觸及機率、台股拆股還原(`_back_adjust_tw`,r<0.6 或 r>1.7 且非除息日的啟發式)全部裸奔;唯一數值 gate 是人工 + 要打 API。→ 工單 001/002/003。
2. **[API 額度] 人工 gate 本身耗額度**。每次驗黃金值 = 打 FinMind;dashboard 每次冷載入 36 呼叫(300/hr 免 token);`watch --interval` 常駐輪詢若間隔太短或清單太大會啃光額度;Finnhub 60/min。已有 `.cache/`(檔案快取)+ `@st.cache_data`(ttl 900/3600)兩層,但**無人盤點過總帳**。→ 工單 001(把數值 gate 離線化,治本)+ 004(呼叫盤點審計)。
3. **[資料源降級] 降級路徑無測試**。yfinance 429 → Finnhub(只有報價無歷史)→ 過期快取保命,這條鏈是雲端存活的命脈,但只在真實故障時被驗證過;Finnhub 免費版缺歷史使 `price_band` 美股 ETF(CRWV/NBIS/OKLO/UFO/XOVR)雲端缺河流圖(README 已知限制);INTL(.L)仍走 yfinance 雲端不穩。→ 工單 005。
4. **[secrets] 現況乾淨但無防再犯機制**。側邊欄自帶金鑰(`c091ce7`)目前只進 session_state/env,未落地——但沒有任何 pre-commit/audit 防未來把金鑰寫進 config.yaml 後誤 commit。→ 工單 004 附帶檢查項(輕量,不另開單)。
5. **[README §5 既列盲點,屬設計而非 bug,不開修復單]**:便宜價=假設非事實(§5.1,已用隱含 P/E 護欄緩解)、EOD 非即時(§5.4)、集中度風險無投組層級警示(§5.6,README §8 列為未來擴充)、聯發科錨定年份敏感度(§5.1)。這些**保持現狀**,只在文件工單裡確認描述仍準確。
6. **[文件 drift,低風險]** `.claude/CLAUDE.md` 寫「~2,260 行、11 個 py、app.py 727 行」與現況有出入;README §7 檔案結構缺 `app.py`、`_gen_watchlist.py`、`_add_pyramid.py`、`watchlist.pdf_seed.yaml` 等;`.gitignore` 註解有 cp950 亂碼(不影響功能)。→ 工單 006。

**禁區重申(所有工單共同繼承)**:`watchlist.yaml`、`watchlist.pdf_seed.yaml`、`config.yaml`、`_calibrations.json`/`_calib_raw.json`/`_fixed_bands.json`/`_new_raw.json`、`custom_watchlist.yaml`、`AI_Investment_Blueprint.pdf`、`.streamlit/config.toml`、`.claude/settings*.json`;金鑰不寫死不 commit;免責聲明不得刪改語氣。

---

## 4. 工單疊(依風險排序;每張獨立、窄、可單獨 close)

### 001 — 估價引擎黃金值 regression 測試(最優先)

- **目標**:把「台積電黃金值人工比對」自動化成**離線**測試:以寫死在測試內的 2330 假設 fixture(從現行 watchlist 複製一份快照)直接呼叫 `aimonitor.valuation.compute_zones`,斷言 便宜 ≈2,228.57 / 大特 ≈1,731.23(容差 ±0.5%),外加五帶單調遞增(大特<便宜<合理<昂貴<瘋狂)與 `classify_region` 邊界行為。
- **允許檔案**:新增 `tests/__init__.py`、`tests/test_golden_valuation.py`;如需 pytest 才可動 `requirements.txt`(建議先用 stdlib `unittest`,零新依賴)。
- **驗收**:`python -m unittest discover tests`(或等效)離線通過;拔網路也能跑;py_compile 綠;**不碰** `aimonitor/` 任何一行。
- **禁區**:不得改引擎遷就測試;不得讀 live `watchlist.yaml` 當 fixture(快照寫死)。
- **API 呼叫評估**:**0 次**——這正是本單的意義:數值 gate 從此不耗 FinMind 額度。

### 002 — 純函數單元測試:ROI 稅費與分類/機率

- **目標**:對 `roi.py`(`_buy_cost`/`_sell_proceeds`/`scenario_roi`:台股證交稅 0.3%+手續費、美股股利預扣 30%、跨幣別)與 `classify.py`(`classify_region` 五帶邊界、`annualized_vol`、`prob_hit_barrier` 已知解析值)補齊離線單元測試,用手算數字當 oracle。
- **允許檔案**:新增 `tests/test_roi.py`、`tests/test_classify.py`。
- **驗收**:離線通過;測試中的手算 oracle 需在註解列出算式供人工複核(紅線 2:數值正確由人拍板)。
- **禁區**:同 001;發現引擎疑似算錯時**停下回報**,不得自行改引擎(那是另開工單的決策)。
- **API 呼叫評估**:0 次。

### 003 — 台股拆股還原 `_back_adjust_tw` 回歸測試

- **目標**:用合成價格序列鎖住還原邏輯的三個行為:(a) 0050 型 1:4 拆股(r<0.6)被還原;(b) 高股息 ETF 大額除息日(有 div record)**不**被誤判為拆股;(c) 正常波動(0.6≤r≤1.7)不觸發。這是 README §4 點名、影響百分位/波動率/殖利率河流圖的關鍵啟發式。
- **允許檔案**:新增 `tests/test_back_adjust.py`。
- **驗收**:離線通過;三情境各至少一案例。
- **禁區**:同 002。
- **API 呼叫評估**:0 次(全合成資料)。

### 004 — API 呼叫與快取盤點(唯讀審計 → 文件)

- **目標**:逐 code path 盤點「dashboard 冷載入 / CLI report 全清單 / screen / watch 每輪」各打 FinMind/yfinance/Finnhub 幾次,對照額度(300/hr、60/min),確認 `@st.cache_data` TTL 與 `.cache/` `max_age_min` 的涵蓋與失效行為,並附帶確認金鑰只存在 env/session_state(不落地)。產出 `docs/api-budget.md`。
- **允許檔案**:新增 `docs/api-budget.md`;**零程式碼變更**。
- **驗收**:文件含每指令的呼叫數上限表 + 「watch 最小安全 interval」建議;若發現超額風險或金鑰落地,**只記錄**,修復另開單。
- **禁區**:不改任何 .py/.yaml。
- **API 呼叫評估**:0 次(純讀 code;不實跑全清單)。

### 005 — 資料源降級路徑離線測試(mock)

- **目標**:用 monkeypatch/mock 模擬 (a) yfinance 429→Finnhub 接手、(b) 全源失敗→過期快取保命、(c) Finnhub 無歷史時 `price_band` 固定帶仍能分類,鎖住 `providers.fetch`/`fetch_us` 的降級順序與錯誤訊息。
- **允許檔案**:新增 `tests/test_providers_fallback.py`。
- **驗收**:離線通過,mock 不發真請求;降級順序斷言與 README ☁ 節描述一致。
- **禁區**:不改 `providers.py`(除非發現 bug,停下回報另開單)。
- **API 呼叫評估**:0 次(全 mock)。依賴 004 的盤點結論,排在其後。

### 006 — 文件 drift 修正(低風險收尾)

- **目標**:同步 `.claude/CLAUDE.md` 的行數/檔案數描述、README §7 檔案結構(補 `app.py`、`_gen_watchlist.py`、`_add_pyramid.py`、`watchlist.pdf_seed.yaml`、`docs/`、`tests/`)、修 `.gitignore` 亂碼註解;並把 001–005 建立的離線測試 gate 寫進 CLAUDE.md「測試現況」節。
- **允許檔案**:`.claude/CLAUDE.md`、`README.md`、`.gitignore`(僅註解)。
- **驗收**:README §4/§5 的方法論與免責文字**一字不動**(紅線 4);只改結構/數字描述。
- **禁區**:不動任何 .py/.yaml/校正產物。
- **API 呼叫評估**:0 次。排最後,等測試落地後一次同步。

---

## 5. 收斂與下一步

- 每張工單走規約:SPEC → executor PLAN/實作 → orchestrator 讀 diff + py_compile + 黃金值(涉估價時)→ reviewer 挑毛病 → orchestrator 逐條查證 → 一個乾淨 commit → close。
- **001 先行**:它把「數值正確第一」從人工+耗額度變成離線自動 gate,是所有後續工單的安全網。
- 本檔產出後**暫停,等人類審核**工單疊與排序,再派 executor。
