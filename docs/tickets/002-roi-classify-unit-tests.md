# 002 — ROI 稅費與分類/機率 純函數單元測試(離線)

狀態:OPEN(等 001 close 後派工)
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

## REPORT(executor 填)
