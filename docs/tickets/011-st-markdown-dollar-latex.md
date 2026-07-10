# 011 — dashboard ROI「投入」行被 Streamlit 誤渲染為 LaTeX(backlog)

狀態:**CLOSED**(2026-07-06,commit 見文末收斂紀錄)
(2026-07-06 dashboard 冒煙時發現,**改動前即存在**,非 009 引入)

## 收斂紀錄(orchestrator)

- Executor 交付:`md_money()` helper(`$`→`\$`,docstring 說明 KaTeX 觸發條件與
  使用時機)+ 投入行換用;全檔盤點 12 個 `money()` 呼叫點與所有 markdown 呼叫,
  僅此一處同字串 ≥2 個 `$`,其餘不改各有明確理由(單 `$` 不觸發/dataframe
  與 metric 不走 markdown),清單見 REPORT。
- **免派獨立 reviewer(orchestrator 裁決)**:diff 為 helper + 單一呼叫點替換,
  盤點清單逐項可查,且 orchestrator 以 preview 實機驗證:投入行渲染為乾淨文字、
  粗體為真 `<strong>`、數學亂排符號(∗∗/斜體字元)消失,免責 caption 未動。
- 收斂 gate:76/76 綠、py_compile 綠、diff 僅白名單、0 API(preview 驗證用
  當日第二次冷載入 ~36 次 FinMind,屬人工驗收非套件行為)。→ CLOSE。

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

## PLAN(executor 填)

1. 在 `money()` 定義旁加 `md_money(x, ccy)` helper:回傳 `money()` 結果並把
   `$` 轉義為 `\$`,只給「同一 markdown 字串內會嵌入 ≥2 個金額」的呼叫點用。
2. 全檔 grep `st.markdown/write/info/warning/error/caption` + `money(` 交集,
   逐一數該字串內 `$` 出現次數(全來自 money() 輸出,檔內無其他字面 `$`)。
3. 只有投報率分頁「投入 … → 買進 … @ …」那行(L462)同一 `st.markdown`
   內有兩個 `money()` → 兩個 `$`,換成 `md_money()`;其餘 money() 呼叫點
   皆為單一 `$`(不觸發 KaTeX)或走 `st.dataframe`/`st.metric`(非 markdown
   渲染路徑),不改。
4. `py_compile` + 全套 unittest 跑一次收斂;不碰 report.py、不動免責文字。

## REPORT(executor 填)

**helper 定義**(插在 `money()` 定義後,app.py L125-139):

```python
def money(x, ccy):
    if x is None:
        return "—"
    return f"{'NT$' if ccy=='TWD' else '$'}{x:,.2f}"


def md_money(x, ccy):
    """money() 的 markdown 安全版:把 `$` 跳脫為 `\\$`。

    Streamlit 的 st.markdown/st.info/... 會用 KaTeX 解析文字,若同一段字串內
    出現兩個以上未跳脫的 `$`(例如「投入 NT$xxx → 買進 … @ NT$xxx」兩個金額),
    會被誤判成行內數學公式,導致金額亂排、`**` 粗體失效。只在會同時嵌入
    ≥2 個 money() 結果的 markdown 呼叫點使用;單一 `$` 不會觸發 KaTeX,無需改用。
    """
    return money(x, ccy).replace("$", "\\$")
```

**改動點(L462,投報率分頁,原 ticket 現象命中處)**:

```diff
-                st.markdown(f"投入 **{money(r['spent'], r['stock_ccy'])}** → 買進 **{sh}** @ {money(r['price'], r['stock_ccy'])}")
+                st.markdown(f"投入 **{md_money(r['spent'], r['stock_ccy'])}** → 買進 **{sh}** @ {md_money(r['price'], r['stock_ccy'])}")
```

**全檔盤點**(`grep money(` × `st.markdown/write/info/warning/error/caption`,
確認檔內除 money() 定義本身外無其他字面 `$`,故 `$` 個數 = 該字串內 money()
呼叫次數):

| 行號 | 呼叫 | money() 次數/$ 個數 | 處理 |
|---|---|---|---|
| L410 comment n/a | `st.error("需要 PyYAML…")` | 0 | 不改(無金額) |
| L462 | `st.markdown("投入 … → 買進 … @ …")` | **2** | **改用 `md_money()`** |
| L470 | `st.info("📌 目標價 ＞ 現價（{money(cur,…)}）…")` | 1 | 不改(單一 $ 不觸發數學模式) |
| L478 | dict value → `pd.DataFrame` → `st.dataframe`(非 markdown 渲染) | 1(但走 dataframe cell,非 KaTeX 路徑) | 不改 |
| L340, L389, L560, L574, L672 | dict value → `st.dataframe` | 各 1,走 dataframe cell | 不改(非 markdown 路徑) |
| L410, L520(`col.metric`), L583(`m1.metric`) | `st.metric()` | 各 1,走 metric widget(非 markdown/KaTeX 路徑) | 不改 |
| L521 | `st.caption(f"現價 {money(d['price'],…)} ｜錨點=…")` | 1 | 不改(單一 $) |
| L284, L291, L300, L310, L362, L366, L380, L418, L420, L422, L424, L428, L429, L431, L433, L501, L506, L526, L536, L540, L547, L591, L594, L601, L606, L614, L632, L646, L652, L660, L688, L727, L729, L732 | 各 `st.markdown/caption/info/warning/error`,均無 `money()`/`$` 出現 | 0 | 不改 |

結論:全檔僅 L462 一處符合「同字串 ≥2 個 `$`」條件,已修正;其餘呼叫點皆
為單一 `$`(不觸發 KaTeX 行內數學)或走 `st.dataframe`/`st.metric`(非
markdown 文字渲染路徑,不受 KaTeX 影響),依工單指示不動。

**Gate**:
- `python -m py_compile app.py`:通過,0 錯誤。
- `python -m unittest discover -s tests`:76 tests, OK(0 failures, 0 errors)。
- 0 API 呼叫(純顯示層字串修改,未新增任何資料源呼叫)。
- 未跑 `monitor.py report` 黃金值比對:本工單只動 app.py 顯示層字串
  (helper + 1 處呼叫點替換),未觸及 `aimonitor/valuation.py`/`classify.py`/
  `roi.py` 任何估價/分類/ROI 邏輯,不適用。
- 未跑 live `streamlit run app.py`(依工單指示,orchestrator 親自用 preview 驗證)。
- 剩餘風險:`md_money()` 僅對 `$` 做字面 replace,若未來 `money()` 格式改變
  (例如加入其他 KaTeX 特殊字元如 `_`/`^`/`\`)需一併檢視;目前僅 `$` 一種
  字元受影響,已覆蓋。
