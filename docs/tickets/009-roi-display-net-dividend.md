# 009 — ROI 顯示端接上股利預扣口徑(backlog)

狀態:OPEN(backlog,尚未排程;007 reviewer P3-A 衍生)

## 背景
工單 007 後,`scenario_roi` 的報酬已含美股股利 30% 預扣(淨額),但
`dividend_yield_pct` 欄位與 `report.py` L165–166 的「已含股利率 X%/年之簡化累積」
文案仍是**毛殖利率**口徑——高息美股標的會出現「顯示殖利率 × 年數 ≠ 表內報酬」的落差。
result dict 已有 `us_div_withholding` 欄位可用(007 刻意 additive 不接顯示)。

## 建議範圍(開工時再細化)
- `report.py::render_roi` 與 `app.py` ROI 分頁:US 標的加註
  「殖利率為毛值;試算報酬已扣 30% 股利預扣」(或顯示淨殖利率)。
- 不改數值邏輯,純顯示;免責聲明不動。

## API 呼叫評估
0 次(純顯示)。
