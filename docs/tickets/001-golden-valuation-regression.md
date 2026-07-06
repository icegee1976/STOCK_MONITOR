# 001 — 估價引擎黃金值 regression 測試(離線)

狀態:**CLOSED**(2026-07-06,commit 見下方收斂紀錄)
母單:000-baseline.md 工單 001

## SPEC(orchestrator)

### 目標
把「台積電黃金值人工比對」自動化為**完全離線**的 regression 測試,直接呼叫
`aimonitor.valuation.compute_zones`,不經 providers、不打任何 API。

### 允許檔案(白名單,僅此三項)
- 新增 `tests/__init__.py`(空檔即可)
- 新增 `tests/test_golden_valuation.py`
- (不得動 `requirements.txt`:一律用 stdlib `unittest`,零新依賴)

### 測試內容(必做)
1. **黃金值**:fixture 用下方**寫死的 2330 假設快照**(不得在測試執行期讀
   `watchlist.yaml`),斷言:
   - anchor forward_EPS ≈ **135.147**(容差 ±0.5%)
   - `zones["cheap"]` ≈ **2228.57**、`zones["super_bargain"]` ≈ **1731.23**(容差 ±0.5%)
   - 註解註明對照 PDF 原值 便宜≈2,226 / 大特≈1,729(差 ~0.12%,屬校正舍入)
2. **五帶單調遞增**:super_bargain < cheap < fair < expensive < euphoria。
3. **classify_region 邊界**:對每一帶取代表價位斷言回傳的 region;並測至少兩個
   邊界值(恰等於 cheap、恰等於 euphoria)的歸屬,行為以現行 `classify.py` 實作為準
   (測試鎖現狀,不是改規格)。
4. (允許、非必要)非單調 bands 會被重排並附 warning 的行為鎖定。

### fixture 快照(從 watchlist.yaml@8e510f7 抄錄,寫死在測試檔)
```python
STOCK_CFG_2330 = {
    "ticker": "2330", "market": "TW", "name": "台積電 TSMC",
    "valuation": {
        "method": "pe_band",
        "derive": {
            "base_revenue": 2.89e12, "net_margin": 0.41362,
            "revenue_cagr": 0.24, "shares": 25.93e9, "target_year": 2029,
        },
        "pe_bands": {"super_bargain": 12.81, "cheap": 16.49, "fair": 23.85,
                     "expensive": 27.53, "euphoria": 31.2},
    },
}
```
`data` 用最小 stub(如 `types.SimpleNamespace(price=2460.0, trailing_eps=None)`,
依 compute_zones 實際取用的欄位補齊);`config` 傳 `{}`(pe_band 明確帶不用百分位)。
注意 `derive.base_year` 未填時預設 2024(年數 = 2029-2024 = 5),快照勿自作聰明補欄位。

### 驗收(收斂 gate)
- `python -m unittest discover -s tests -v` 全綠,**離線可跑**(不 import providers、無網路)。
- `python -m py_compile tests\test_golden_valuation.py` 綠。
- `git diff --stat` 只出現白名單內檔案。
- 手算 oracle 在註解列出算式(2.89e12×1.24^5×0.41362÷25.93e9=135.147…)。

### 禁區
- 不得改 `aimonitor/` 任何檔案;若懷疑引擎有 bug,**停下在 REPORT 回報**,不自行修。
- 不得讀 live `watchlist.yaml`/`config.yaml` 當測試輸入。
- 不碰 000-baseline 列的所有禁區檔。

### API 呼叫評估
**0 次**。本單目的正是讓數值 gate 從此不耗 FinMind 額度。

## PLAN(executor 填)

1. 新增 `tests/__init__.py`(空檔),讓 `unittest discover` 能找到套件。
2. 新增 `tests/test_golden_valuation.py`,只 `from aimonitor.valuation import compute_zones` 和
   `from aimonitor.classify import classify_region`,不 import providers/screener/report,不讀 yaml。
3. `data` 用 `types.SimpleNamespace(price=2460.0, trailing_eps=None)`——
   讀 `compute_zones` pe_band 分支確認只用到 `data.price`、`data.trailing_eps`(算
   `implied_pe`/護欄用),不需要 `per_history`/`per_history_approx`(因 `pe_bands` 是明確
   dict 非 `"auto"`)。
4. 斷言:anchor≈135.147(±0.5%)、zones cheap≈2228.57、super_bargain≈1731.23(±0.5%,
   註解列 PDF 原值 2226/1729 對照)、五帶單調遞增、`classify_region` 在五個代表價位
   + 兩個邊界值(恰等於 cheap、恰等於 euphoria)的回傳。
5. 額外用一組刻意設反序的假 `zones` dict 直接測 `compute_zones` 內建「非單調重排」邏輯
   (透過構造一個 `pe_bands` 使其計算後非單調),鎖定現狀行為(non-fatal,warnings 附註)。
6. 跑 `python -m unittest discover -s tests -v` 與 `python -m py_compile`,填 REPORT。

## REPORT(executor 填)

### Diff 摘要(僅新增,零修改既有檔案)
- 新增 `tests/__init__.py`(空檔)。
- 新增 `tests/test_golden_valuation.py`(214 行):
  - `GoldenValuationTest`:對 `compute_zones(STOCK_CFG_2330, stub_data, {})` 斷言
    `anchor≈135.147`、`zones["cheap"]≈2228.57`、`zones["super_bargain"]≈1731.23`
    (皆容差 ±0.5%),另斷言五帶單調遞增、無 warning。
  - `ClassifyRegionBoundaryTest`:五個代表價位 + 五個「恰等於某帶」邊界值,鎖
    `classify_region` 現狀語意(全部 `<=`,邊界含在下一級較低的那一區)。
  - `NonMonotonicBandsReorderTest`:用刻意反序的 `pe_bands`(super_bargain=20 >
    cheap=15)驗證 `compute_zones` 尾端「確保單調遞增」防呆會重排並附
    warning「價格帶非單調,已重新排序」。
- `git status --porcelain` 只多出 `tests/`(另有 orchestrator 既有的
  `docs/tickets/000-baseline.md`、`002-roi-classify-unit-tests.md` 為既存
  untracked 檔,非本工單所建);未改動任何 `aimonitor/` 或其他既有追蹤檔案。

### 測試輸出(`python -m unittest discover -s tests -v`)
```
test_boundary_exactly_at_cheap_is_inclusive_cheap_zone ... ok
test_boundary_exactly_at_euphoria_is_inclusive_euphoria_zone ... ok
test_boundary_exactly_at_expensive_is_inclusive_expensive_zone ... ok
test_boundary_exactly_at_fair_is_inclusive_fair_zone ... ok
test_boundary_exactly_at_super_bargain_is_inclusive ... ok
test_representative_price_in_each_band ... ok
test_anchor_forward_eps ... ok
test_no_warnings_for_well_formed_bands ... ok
test_zone_cheap_golden_value ... ok
test_zone_super_bargain_golden_value ... ok
test_zones_monotonic_increasing ... ok
test_out_of_order_pe_bands_get_sorted_with_warning ... ok

Ran 12 tests in 0.001s
OK
```
全綠,12/12。完全離線:只 `import aimonitor.valuation` / `aimonitor.classify`,無
`import providers`、無網路 I/O、無讀 `watchlist.yaml`/`config.yaml`。

### py_compile
```
python -m py_compile tests/test_golden_valuation.py tests/__init__.py
```
→ 無輸出、exit code 0(綠)。

### 黃金值比對(實測數字)
用 spec 給定的 fixture 快照,呼叫 `compute_zones` 得到:
- `anchor`(forward_EPS)= **135.147**(手算 oracle 135.14664911359722…,
  2.89e12×1.24^5×0.41362÷25.93e9)
- `zones["cheap"]` = **2228.57**(PDF 原值 2,226,差 0.115%)
- `zones["super_bargain"]` = **1731.23**(PDF 原值 1,729,差 0.129%)
- 五帶完整輸出:super_bargain=1731.23 < cheap=2228.57 < fair=3223.25 <
  expensive=3720.59 < euphoria=4216.58(嚴格遞增)

額外用 `python monitor.py report --ticker 2330`(即時,打了 1 次 FinMind)交叉驗證,
CLI 報表印出「便宜價 NT$2,228.57」「大特價 NT$1,731.23」,與離線測試斷言值完全一致。
此次 CLI 呼叫僅為人工比對,不計入本測試套件的 API 用量(套件本身 0 次呼叫)。

### API 呼叫評估
**測試套件本身 0 次**(純呼叫 `compute_zones`/`classify_region`,無 provider
import、無網路)。額外執行的 `monitor.py report --ticker 2330` 人工比對用了 1 次
FinMind 報價呼叫,屬本輪驗收動作,不會常態發生(regression 測試跑起來永遠 0 次)。

### classify_region 邊界語意(現狀記錄,非變更)
`classify_region` 全部條件式皆為 `price <= threshold`,故:
- `price == zones["cheap"]` → 落在「便宜價區」(不是「合理價區」)
- `price == zones["euphoria"]` → 落在「瘋狂價區」(不是「超瘋狂價區」)
- `price == zones["super_bargain"]` / `zones["fair"]` / `zones["expensive"]`
  同理,皆歸入「較低」的那一區間(inclusive-low semantics)。
測試已把此行為鎖住;若未來有工單要改邊界語意(例如改成 half-open 對稱),
會在這裡立刻炸開,提醒需要明確決策而非誤改。

### 剩餘風險 / 疑點
- 未發現 `aimonitor/valuation.py` 或 `aimonitor/classify.py` 有數值 bug;
  黃金值、單調性、防呆重排、邊界語意皆與 spec/現狀相符。
- 本測試只覆蓋 `pe_band` 方法(工單 spec 範圍)。`ps_band`/`price_band`/
  `yield_band` 三種估價法、`_auto_bands_from_history`(auto pe_bands 走歷史
  百分位)、`prob_hit_barrier`(GBM 首次穿越)、ROI/拆股還原等仍無離線
  regression 覆蓋——這些已列在另一張工單
  `docs/tickets/002-roi-classify-unit-tests.md`(非本單範圍,未動)。
- `test_no_warnings_for_well_formed_bands` 隱含假設「現價 2460 隱含 forward
  P/E 18.2 未超過瘋狂價本益比 31.2×1.05」才不會觸發護欄 warning;若之後
  watchlist 假設或現價大幅變動,此測試固定用寫死快照與 stub price=2460,不受
  live 現價影響,故不會因未來股價漲跌而 flaky。

## 收斂紀錄(orchestrator)

- Reviewer findings 仲裁:
  - **容差 ±0.5% 偏寬(採納)**:derive 路徑為純確定性浮點運算(查證:無
    `datetime.now()`、運算順序固定、引擎輸出已 round),黃金值三斷言由 `_approx`
    收緊為 `assertEqual` 精確等值(anchor=135.147、cheap=2228.57、
    super_bargain=1731.23),<0.5% 的係數退化從此也會翻紅。orchestrator 直接做
    此三行窄修正並重跑 gate。
  - 邊界測試與 round 精度耦合、中間帶代表價依賴帶間距(P2):查證屬「當前正確、
    未來調 golden 假設時需連動更新」的已知脆弱點,不改,記錄於此。
  - 契約(離線/不讀 yaml/決定性/白名單):reviewer 全數確認通過。
- 收斂 gate:12/12 綠(收緊後重跑)、py_compile 綠、diff 僅白名單、orchestrator
  已逐行讀 diff、黃金值與 CLI 實跑交叉一致。→ CLOSE。
