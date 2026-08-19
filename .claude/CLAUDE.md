# STOCK_MONITOR — 協作規約(Tier 1:Orchestrator / Executor / Reviewer)

## 專案
Python **Streamlit + CLI** 的美股/台股 AI＋太空 成長股/ETF **價格帶監測器**。把孫慶龍《AI 投資藍圖》的估價法(前瞻本益比河流圖)程式化,自動把現價分到 **大特價 / 便宜 / 合理 / 昂貴 / 瘋狂** 五帶,並做情境 ROI 試算。
**定位:資訊 / 教育工具,不是投資建議。**

## 技術棧
- Python 3.11+;deps:`streamlit>=1.49`、`plotly`、`pandas`、`PyYAML`、`yfinance`(+ 選用 `rich`、`win10toast`/`plyer`)。
- 兩入口共用 `aimonitor/` 引擎:CLI `monitor.py`(report/screen/roi/watch/bands)、Dashboard `app.py`(`streamlit run`)。
- 資料源:台股 **FinMind**(免金鑰,300/hr,`FINMIND_TOKEN`→600)、美股 **yfinance**(雲端易 429)→ **Finnhub** 備援(`FINNHUB_API_KEY`,60/min,只有報價無歷史)。
- 引擎:`aimonitor/{providers,valuation,classify,roi,screener,report,history_store}.py`。~3,340 行、12 個 py(不含 tests/ 十檔 ~4,500 行);`providers.py` 最大(含 EOD 快取/歷史庫接線/TWSE 備援)。

## 指令
- CLI:`python monitor.py report --ticker 2330` / `screen` / `roi NVDA 300000`
- Dashboard:`streamlit run app.py`(或 `run_dashboard.bat`,localhost:8501)
- **測試 gate**:`python -m unittest discover -s tests`(210 題,**完全離線 0 API**,秒級)
- **語法 gate**:`python -m py_compile <改到的檔>`

## 測試現況
**tests/ 離線套件 210 題**(2026-08,工單 001–017;stdlib unittest,零依賴、拔網路可跑):
黃金值 regression(台積電 anchor 135.147 / 便宜 2228.57 / 大特 1731.23 精確鎖定,對照 PDF ≈2,226/≈1,729)、
ROI 稅費(證交稅/手續費/美股股利預扣 30%/跨幣別)、分類邊界、GBM/√252、拆股還原、
資料源降級鏈(快取/Finnhub→yfinance/過期快取保命/FinMind 呼叫數)、台股 EOD-aware 快取、
本地 sqlite 歷史庫與增量(跨增量拆股)、缺日/品質警告、TWSE/TPEx 備援(不寫 blob/尺度護欄/負向 memo)。收斂 gate:
1. **測試**:`python -m unittest discover -s tests` 全綠(黃金值已自動化,不再耗 API)。
2. **語法**:`python -m py_compile` 改到的檔零錯誤。
3. **數值正確**(命脈):改估價 / 分類 / ROI 仍須人工比對一次 CLI 黃金值交叉確認。
4. **冒煙**:CLI 單標的 / dashboard 啟動不崩。
5. **人工驗收**:財務合理性由人類判斷。
> API 額度總帳見 `docs/api-budget.md`(每指令呼叫數、快取層、watch 安全 interval)。

## 你的角色:Orchestrator —— 這個主 session
不親自大量寫。診斷、切窄工單(`docs/tickets/<id>.md`)、交 executor、**自己讀 `git diff`**、跑 `py_compile` + 對黃金值、逐條查證 reviewer findings、決定收斂、負責 close。Executor / Reviewer 見 `.claude/agents/`。Reviewer **無決策權**。

## 🚑 紅線
1. **顧 API 額度**:FinMind 300/hr、Finnhub 60/min、yfinance 易 429(曾 402 額度用罄)。改動前評估新增呼叫數,**尊重既有 `@st.cache_data` 快取**,不要為了方便狂打 API。
2. **數值正確第一**:估價公式、五價格帶分類、ROI(證交稅 / 手續費 / 美股股利預扣 30% / 匯率 / 股利)、百分位 / 波動率(GBM 首次穿越機率)、**拆股自動還原**邏輯 —— 錯了就給錯買賣訊號。動這些一律對黃金值驗證。
3. **不改使用者的假設 / 選股**:`watchlist.yaml`、`config.yaml` 的估價假設與清單是**使用者判斷**,不是 bug,未經明確要求不動。
4. **保留免責聲明**:資訊 / 教育工具,非投資建議;不得改寫成投資建議語氣。
5. **Additive**:不破壞既有 CLI / dashboard、資料源降級(重試 + 過期快取保命)、多幣別 ROI。

## 禁區(未經明確工單不得更動)
- `watchlist.yaml`、`watchlist.pdf_seed.yaml`、`config.yaml`(使用者資料 / 假設;改帶動全體估值)
- `_calibrations.json`、`_calib_raw.json`、`_fixed_bands.json`、`_new_raw.json`(校正產物,用 `_gen_watchlist.py` 重生,勿手改)
- `custom_watchlist.yaml`(gitignore 的個人資料)、`AI_Investment_Blueprint.pdf`(來源簡報)
- **API 金鑰 / secrets:不得寫死、不得 commit**;走 Streamlit secrets / 環境變數 / 側邊欄「自帶金鑰」
- `.streamlit/config.toml`、`.claude/settings*.json`、`__pycache__/`、`.cache/`

## 鐵則
- 改 `app.py`(727 行)/ `providers.py`(399 行)前先 Read 目標區段再改。
- 每輪要收斂;財務判斷、風險、假設接受,一律人類最終拍板。
