# 009 — ROI 顯示端:美股股利毛/淨並列(顯示,不改數值)

狀態:**CLOSED**(2026-07-06,commit 見文末收斂紀錄)
(人類已拍板:「選更能反映現實幫助判斷的做法」)
決策(orchestrator 落地):**毛、淨並列**——毛殖利率是市場慣例(和券商 App 對得上),
稅後淨額才是台灣投資人實際入袋;兩個數字各答一個問題,並列才不用心算。
不改任何數值邏輯,`us_div_withholding` 欄位 007 已備好。

## SPEC(orchestrator)

### 目標
1. `aimonitor/report.py::render_roi`(L165–166):現行
   `(已含股利率 X%/年 之簡化累積)`,當 `r.get("us_div_withholding")` 非 None 且
   `dividend_yield_pct` 非零時,改為毛淨並列,例:
   `(已含股利率 2.0%/年 之簡化累積;美股稅後 ≈1.4%/年,報酬已按 30% 股利預扣計算)`
   —— 淨 = 毛 × (1 − rate),稅率 % 由 `us_div_withholding` 動態算(勿寫死 30)。
   非美股(欄位 None)維持原樣一字不動。
2. `app.py` ROI 分頁 st.info(L466–468):句尾「含股利與費稅」後,若
   `r.get("us_div_withholding")` 非 None 補一句:
   `美股股利已按 X% 預扣稅計算(上方殖利率如有顯示為毛值)。`
3. 新增離線顯示測試 `tests/test_report_roi_display.py`:構造 stub result dict 直接呼叫
   `render_roi`,patch `report.HAS_RICH=False` + `contextlib.redirect_stdout` 擷取輸出,
   斷言 (a) US(us_div_withholding=0.30、dividend_yield_pct=2.0)輸出含「稅後」與
   「30%」與淨值 1.4;(b) TW(None)輸出**不**含「稅後」;(c) 免責/其他行未受影響。

### 允許檔案(白名單)
- `aimonitor/report.py`(僅 render_roi 的股利註記行,≤6 行 diff)
- `app.py`(僅 ROI 分頁 st.info 一處,≤4 行 diff;改前先 Read 目標區段)
- 新增 `tests/test_report_roi_display.py`

### 禁區
- **DISCLAIMER 一字不動**(紅線 4);不改 roi.py/valuation/classify/providers;
  不動 L693 自訂清單分頁(只畫圖,無文字);不打 API。

### 驗收
- 71+新增 全綠、py_compile 綠;diff 僅白名單;render_roi 對 TW 輸出與改前逐字相同
  (測試鎖住)。0 API 呼叫(顯示測試全 stub,不跑 CLI live)。

## PLAN(executor 填)

1. `report.py::render_roi` 股利註記 `if r["dividend_yield_pct"]:` 區塊內,取
   `rate = r.get("us_div_withholding")`;非 None 時算 `net_yield = 毛 * (1-rate)`,
   輸出「毛值...;美股稅後 ≈淨值%/年,報酬已按 rate*100% 股利預扣計算」;
   None(非美股)時維持原 `_p` 呼叫逐字不變。
2. `app.py` ROI 分頁 st.info:句尾既有文字前先組出 `_div_note`(條件式,
   `us_div_withholding is not None` 才有值),用 `+ (f"\n\n{_div_note}" if _div_note else "")`
   接在原 f-string 尾端,不改動既有兩句文字。
3. 新增 `tests/test_report_roi_display.py`:stub result dict(比照 `roi.py` 回傳
   形狀),patch `report.HAS_RICH=False` 讓 `_p` 走 `print()` 分支,用
   `contextlib.redirect_stdout` 擷取,斷言 US 含「稅後」/「30%」/「1.4%」、
   TW 不含「稅後」且股利行逐字比對現行文案、其他行(標題/情境說明/DISCLAIMER)不受影響。
4. Gate:`python -m py_compile aimonitor/report.py app.py tests/test_report_roi_display.py`、
   `python -m unittest discover -s tests`。全程 0 API 呼叫,不跑 CLI/dashboard live。

## REPORT(executor 填)

### DIFF 摘要
- `aimonitor/report.py`(render_roi 股利註記行,+6/-1 行):US 且
  `dividend_yield_pct` 非零時,毛淨並列(淨值/稅率 % 皆由 `us_div_withholding`
  動態算,無寫死 30);`us_div_withholding is None`(非美股)分支維持原逐字輸出。
- `app.py`(ROI 分頁 st.info,+4 行淨增,只動這一處):組出條件式 `_div_note`
  (`us_div_withholding is not None` 才非空字串),用字串相加接在既有 st.info
  文字尾端;既有兩句文字**一字未動**。
- 新增 `tests/test_report_roi_display.py`(3 個測試):US 毛淨並列文案、
  TW baseline 不含「稅後」且逐字比對現行文案、其他行(標題/情境提示/
  DISCLAIMER 常數)未受影響。

### 實際跑過的指令與結果
- `python -m py_compile aimonitor/report.py app.py tests/test_report_roi_display.py`
  → `PY_COMPILE_OK`(零錯誤)。
- `python -m unittest discover -s tests` → 改前 71 tests OK;改後
  **75 tests OK**(本單新增 3 個 `test_report_roi_display.py`;另 1 個來自
  working tree 既有、非本單改動的 `tests/test_providers_fallback.py` 變更,
  與本工單無關,亦已跑過 py_compile 前的 `git status` 確認未被本單觸碰)。
  單獨跑 `python -m unittest tests.test_report_roi_display -v` → 3/3 OK。
- `git diff -- aimonitor/report.py app.py` 逐條核對,確認僅動白名單允許範圍、
  DISCLAIMER 常數字串完全未變動(`report.DISCLAIMER` 內容比對通過)。

### 黃金值比對
不適用 —— 本工單純顯示層(f-string 文案),未動 `roi.py` / `valuation.py` /
`classify.py` 任何數值計算,`us_div_withholding` / `dividend_yield_pct` 皆沿用
既有欄位原值,不需另跑 `monitor.py report --ticker 2330`(工單要求 0 API)。

### 新增 API 呼叫評估
0。測試全用 stub dict 直接呼叫 `render_roi`,未 import `providers`/未讀
`watchlist.yaml`/`config.yaml`,未觸發任何網路呼叫。

### 剩餘風險
- `app.py` 的 st.info 條件式改動未經 `streamlit run app.py` 冒煙(工單明確要求
  不跑 live CLI/dashboard),僅靠 py_compile + 邏輯核對;若 orchestrator 需要
  視覺驗收建議另外手動跑一次。
- `tests/test_report_roi_display.py` 未覆蓋「dividend_yield_pct 為 0 但
  us_div_withholding 非 None」情境(例如美股殖利率 0% 時,`if r["dividend_yield_pct"]:`
  整段跳過,不會印任何股利行)——此為既有邏輯(0 值視為 falsy 不顯示),行為
  與改動前一致,故未特別新增案例,但提醒 orchestrator 知悉此邊界不受本單影響。

## 收斂紀錄(orchestrator)

- Reviewer(聚焦審)結論:無 P1。仲裁:
  - **P2 採納**:測試對 DISCLAIMER 的斷言原為空驗證(render_roi 不印該常數,
    斷言永遠綠)→ 改為 `DisclaimerConstantLockTest` **逐字等值鎖整個常數**,
    紅線 4 從此有可執行防護(改一個字就翻紅)。
  - **P3(a) 查證不改**:`us_div_withholding==0.0` 只可能來自使用者在 config 明確
    覆寫 `fees.us.dividend_withholding: 0`(如租稅協定情境),此時顯示
    「已按 0% 預扣稅計算」語意正確且能確認覆寫生效。None ⇔ 非美股,約定清楚。
  - **P3(b)(c) 不改**:毛值未格式化屬改前既有顯示風格;substring 斷言現況安全。
  - rich markup / Streamlit markdown 安全性、TW 逐字不變、app.py 既有兩句
    一字未動:reviewer 全數確認。
- 收斂 gate:76/76 綠、py_compile 綠、diff 僅白名單、免責文字 git diff 零命中、
  orchestrator 已逐行讀 diff。app.py st.info 未做 live 冒煙(0 API 原則),
  py_compile + 邏輯核對 + reviewer 雙讀替代;下次啟動 dashboard 時可順眼驗收。
  → CLOSE。
