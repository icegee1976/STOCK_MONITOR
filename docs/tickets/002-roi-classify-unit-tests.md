# 002 — ROI 稅費與分類/機率 純函數單元測試(離線)

狀態:**CLOSED**(2026-07-06,commit 見下方收斂紀錄)
母單:000-baseline.md 工單 002

## ⚠ 開單時的新發現(orchestrator,2026-07-06)
README §5.5 與 `.claude/CLAUDE.md` 紅線宣稱 ROI「內含美股股利預扣 30%」,但全 codebase
無任何預扣實作(`roi.py` 的 `divs = shares × per_share_div × yrs` 直接用 yfinance 原值,
無 0.7 因子;grep `0.7|預扣|withhold` 僅命中無關文案)。**文件與引擎不符**。
修向(補實作=改數字 vs 改文件=承認未含)屬財務判斷 → **人類拍板,另開單**。
**本單不測美股股利,不修此問題。**

## SPEC(orchestrator)

### 目標
對 `aimonitor/roi.py` 與 `aimonitor/classify.py` 的純函數補離線單元測試,
手算數字當 oracle(算式寫在註解供人工複核)。

### 允許檔案(白名單)
- 新增 `tests/test_roi.py`、`tests/test_classify.py`(stdlib unittest,零新依賴)

### 測試內容
**test_roi.py**(fees fixture 寫死:tw brokerage=0.001425、discount=1.0、tax_sell=0.003;us commission=0):
1. `_buy_cost` 台股整股:price=100、capital=100,000 → shares=998(int 截斷)、
   spent=998×100×1.001425=99,942.2215。
2. `_buy_cost` 美股碎股:price=100、capital=10,000 → shares=100.0、spent=10,000;
   commission=1.0 時 spent=10,001。
3. `_sell_proceeds` 台股:998 股 @120 → 119,760×(1−0.001425−0.003)=119,230.062
   (驗證證交稅 0.3% 在賣出端)。
4. `scenario_roi` 跨幣別:TWD 320,000 投美股(fx=32、price=100)→ 內部資本=US$10,000、
   shares=100、`fx_note=True`;同幣別時 `fx_note=False`。
5. `scenario_roi` 防護:price=None 或 ≤0 → 回 error dict;資金不足 1 股(台股)→ error。
6. INTL 市場 `per_share_div=0`(累積型不雙計)——用 market="INTL" 斷言股利貢獻為 0。
   (**不測** 美股股利金額,見上方新發現。)

**test_classify.py**:
1. `classify_region` 六區:zones={sb:10, cheap:20, fair:30, expensive:40, euphoria:50};
   price 5→大特價區、10→大特價區(邊界含)、20→便宜價區(邊界含)、25→合理價區、
   50→瘋狂價區、50.01→超瘋狂價區。
2. `prob_hit_barrier`:barrier≥price→1.0;vol=None/0→None;years≤0→None;
   手算一例:price=100、barrier=80、vol=0.3、T=1 → x=ln1.25≈0.223144、
   P=2Φ(−0.74381)≈0.4569(容差 ±0.002,查表值,註解附算式)。
3. `annualized_vol`:樣本 <30 → None;常數序列(≥31 筆)→ 0.0。
4. `analyze`:`is_buy` 恰在 cheap 邊界為 True;`drop_to_cheap_pct` 永遠 <100
   (price=100、cheap=20 → 80.0)。

### 驗收
- `python -m unittest discover -s tests -v` 全綠(含 001 的測試),離線可跑。
- py_compile 綠;`git diff --stat` 只有白名單檔案。
- 每個 oracle 數字旁有手算算式註解。

### 禁區
- 不改 `aimonitor/`;引擎疑點(含上方股利預扣)只寫 REPORT 不修。
- 不讀 live yaml、不打網路。

### API 呼叫評估
0 次。

## PLAN(executor 填)

1. Read 本工單 SPEC + `aimonitor/roi.py`(96 行)、`aimonitor/classify.py`(109 行)全文,
   確認 `_buy_cost`/`_sell_proceeds`/`scenario_roi`/`classify_region`/`prob_hit_barrier`/
   `annualized_vol`/`analyze` 的簽名與現狀語意。
2. 逐題用 `python -c` 手算並跑一次真實函式邏輯(不落地檔案),核對每個 SPEC oracle
   數字,發現 `_buy_cost` TW 案例 SPEC 原文 99,942.2215 與實際手算/引擎值
   99942.215 有出入(見 REPORT 疑點)。
3. 新增 `tests/test_roi.py`(11 案例:_buy_cost×3、_sell_proceeds×2、scenario_roi
   跨幣別×2、防護×3、INTL 股利×1)與 `tests/test_classify.py`(15 案例:
   classify_region 六區×6、prob_hit_barrier×5、annualized_vol×2、analyze×2)。
   `data` 全部用 `types.SimpleNamespace` stub,不讀 yaml、不 import providers。
4. 跑 `python -m unittest discover -s tests -v`(全 38 題,含 001 的 12 題)確認全綠;
   `python -m py_compile` 兩新檔 + 兩顆引擎檔確認語法零錯誤(引擎檔僅語法檢查,
   未修改任何一行)。
5. 跑 `python monitor.py report --ticker 2330` 對照黃金值(便宜 2228.57 / 大特 1731.23)
   確認未受影響(本單完全沒碰 aimonitor/,理論上不會變動,仍實測驗證)。

## REPORT(executor 填)

### 改動檔案(白名單內,零 aimonitor/ 改動)
- 新增 `tests/test_roi.py`(11 個測試案例)
- 新增 `tests/test_classify.py`(15 個測試案例)
- `git diff --stat` 對已追蹤檔案為空(兩檔皆為新增的 untracked 檔案,未修改任何既有檔)

### 測試輸出
`python -m unittest discover -s tests -v`:
```
Ran 38 tests in 0.002s
OK
```
拆分:`tests/test_roi.py` 11 個全綠、`tests/test_classify.py` 15 個全綠、
`tests/test_golden_valuation.py`(001 交付,未改動)12 個全綠。

`python -m py_compile tests/test_roi.py tests/test_classify.py aimonitor/roi.py aimonitor/classify.py`
→ 零錯誤(後兩者僅語法檢查,未修改內容)。

### 關鍵 oracle 實測值
- `_buy_cost` TW:price=100, capital=100000 → shares=998, spent=99942.215
  (int(100000/100.1425)=998;998×100×1.001425=99942.215)
- `_sell_proceeds` TW:998股@120 → 119760×(1−0.001425−0.003)=119230.062
- `prob_hit_barrier(100, 80, 0.3, 1)` = 0.4569903175523975
  (x=ln1.25=0.223144, z=−0.743812, P=2Φ(z);查表容差 ±0.002 內符合 SPEC 的 ≈0.4569)
- `annualized_vol` 31 筆常數收盤價 → 30 個對數報酬皆為 0 → pstdev=0 → vol=0.0
- `scenario_roi` 跨幣別:TWD 320,000 / fx=32 / US price=100 → capital_in_stock=10000,
  shares=100.0, spent=10000.0, fx_note=True(與同幣別對照組 fx_note=False)
- `monitor.py report --ticker 2330`:便宜價 NT$2,228.57 / 大特價 NT$1,731.23
  (與 test_golden_valuation.py 及 001 黃金值一致,PDF≈2,226/≈1,729 差異屬既有校正舍入,非本單引入)

### 疑點(僅記錄,未修)
1. **工單 SPEC 第 27 行筆誤**:`_buy_cost` 台股案例 SPEC 原文寫
   `spent=998×100×1.001425=99,942.2215`,但實際手算與引擎現狀跑出的值為
   `99942.215`(998×100=99800;99800×0.001425=142.215;99800+142.215=99942.215)。
   SPEC 多寫了一位小數。已在 `test_roi.py::test_tw_whole_share_truncation`
   註解中記錄此落差,測試斷言採**實際手算+引擎現狀值**(99942.215),
   非逐字照抄 SPEC 文字。此為 SPEC 筆誤,非 `aimonitor/roi.py` 的 bug
   (逐行核對 `_buy_cost` 程式碼與獨立 `python -c` 手算完全吻合)。
2. 沿用 SPEC 已知記錄的美股股利預扣 30% 文件/引擎不符問題(見工單頭部),
   本次測試刻意迴避,未新增額外發現。
3. 未發現其他 `aimonitor/roi.py` / `aimonitor/classify.py` 邏輯疑點。

### API 呼叫評估
0 次(全離線,`data` 皆用 `types.SimpleNamespace` stub;`monitor.py report`
驗證步驟屬人工冒煙非測試套件的一部分,套件本身不觸網)。

### 剩餘風險
- 未 commit(依指示)。
- `tests/test_roi.py` 的 INTL 股利測試僅驗證「貢獻為 0」的分支邏輯,
  未涵蓋 INTL market 在 `_buy_cost`/`_sell_proceeds` 的稅費路徑(現狀走 US 分支,
  SPEC 未要求,故未測)。

## 收斂紀錄(orchestrator)

- Reviewer findings 仲裁(無 P1):
  - **P2-3 採納**:252 年化因子原本零保護(常數序列 0×任何因子=0 無鑑別力)。
    orchestrator 補 `test_alternating_series_locks_sqrt252_annualization_factor`:
    31 筆 100/110 交替 → pstdev=ln(1.1),vol=ln(1.1)×sqrt(252)≈1.5130022(手算 oracle)。
  - **P3-1 採納**:刪除 test_known_case 中拿實作驗實作的 assertEqual(erf 重算)四行
    (且引擎有 clamp、測試式沒有,日後改參數會偽陽);保留查表 oracle(0.4569,±0.002)。
  - **P2-4 採納**:INTL 股利測試補斷言未 round 的 value_in_stock_ccy==10000.0。
  - **P2-1、P2-2 不改**:commission 加法已由 with_commission 案例鎖住;shares-guard
    突變情境測試仍會以 exception 形式翻紅,鑑別力可接受。
  - SPEC 筆誤(99,942.2215→99942.215)確認為 orchestrator 手誤,executor 處理正確。
- 收斂 gate:39/39 綠(含 001 的 12 題)、py_compile 綠、diff 僅白名單、
  orchestrator 已逐行讀 diff、黃金值未受影響(本單零 aimonitor/ 變動)。→ CLOSE。
