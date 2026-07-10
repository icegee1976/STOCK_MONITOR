# 011 — dashboard ROI「投入」行被 Streamlit 誤渲染為 LaTeX(backlog)

狀態:OPEN(backlog;2026-07-06 dashboard 冒煙時發現,**改動前即存在**,非 009 引入)

## 現象
投報率分頁的「投入 **NT$299,716.04** → 買進 **124 股(0張+124股)** @ NT$2,415.00」
一行,因同一 markdown 字串內出現**兩個 `$`**,被 Streamlit 的 KaTeX 當成行內數學模式:
金額變斜體亂排、`**` 粗體失效顯示成 `∗∗`。美股(`$207.65`)同理。

## 修向候選(開工時再定)
- 該行改用 `st.markdown(..., unsafe_allow_html=False)` 前先把 `$` 跳脫為 `\$`;或
- 金額顯示層(`money()` 進 dashboard 的路徑)輸出 `\$`;或
- 該行不走 markdown(改 st.text / st.write 純字串)。
影響面:凡 app.py 中同字串含兩個 `$` 的 markdown 都要巡一遍。

## 允許檔案(屆時)
- `app.py`(顯示層);不碰引擎與免責文字。

## API 呼叫評估
0 次(純顯示)。
